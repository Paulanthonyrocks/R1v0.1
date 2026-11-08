# Route One (R1v0.1)

Real-time traffic video analytics: a FastAPI backend ingests camera/RTSP/file feeds in
separate processes, runs YOLOv8 detection + tracking + cross-camera ReID on GPU workers,
derives KPIs and safety incidents (wrong-way, hard braking, congestion, VRU near-miss),
and streams annotated JPEG frames and telemetry to a Next.js dashboard over a binary
msgpack WebSocket. State and inter-process queues live in Redis; persistence is SQLite.

Backend API title/version: `Route One Hub - Backend API` v1.1.0 (`backend/app/main.py`).

## Repository layout

```
R1v0.1/
├── backend/                  FastAPI backend (Python 3.10+)
│   ├── app/
│   │   ├── main.py           app factory, lifespan, middleware stack, router wiring
│   │   ├── config.py         YAML config loader (configs/config.yaml)
│   │   ├── database.py       SQLite (aiosqlite/SQLAlchemy) init; optional MongoDB
│   │   ├── routers/          18 APIRouters (see API surface below)
│   │   ├── services/         FeedManager, ConnectionManager users, analytics, incidents,
│   │   │                     evidence/privacy/forensic, retention, signals, weather, ...
│   │   ├── core/             worker pipeline: ingestion_worker, inference_worker,
│   │   │                     core_module (detect/track/ReID/speed), tracking, analytics_worker,
│   │   │                     database_writer
│   │   ├── ml/               reid_model, anomaly_detector, traffic_predictor, route_optimizer,
│   │   │                     pavement_analysis/ (offline subsystem), segmentation/ (unused)
│   │   ├── websocket/        connection_manager (per-client queues, broadcast)
│   │   ├── middleware/       logging, rate limit, security headers
│   │   └── utils/            redis_client, shared_frame_buffer (SHM), tunnel_auth, ...
│   ├── configs/              config.yaml (all tunables), features.yaml (feature flags)
│   ├── models/               yolov8n.pt, pavement model
│   ├── data/                 vehicle.db (SQLite), snapshots/, processed_videos/, evidence/
│   ├── keys/                 tunnel_auth.token, evidence.key  (gitignored secrets)
│   ├── logs/                 backend_main.log, backend_ml.log, backend_services.log,
│   │                         backend_database.log, backend_worker.log, backend_video_ws.log,
│   │                         uvicorn_access.log, uvicorn_error.log (routing in config.yaml `logging:`)
│   ├── scripts/              model export (ONNX/TensorRT), benchmarks, verify_* probes
│   ├── tests/ + app/tests/   pytest suites
│   ├── data_ingestion/       offline dummy-CSV/data generators (not part of the live pipeline)
│   └── requirements.txt
├── hosting/                  Next.js 16 frontend ("frontend" package, port 3000)
│   ├── app/                  app-router pages (see below) + api/v1/[[...path]]/route.ts proxy
│   ├── components/           dashboard components, ui/ (radix), map, landing
│   ├── lib/                  api/APIClient.ts, websocket/WebSocketClient.ts, useVideoSocket.ts,
│   │                         videoStreamManager.ts, auth/ (Firebase), services/*.ts
│   ├── public/workers/       video-worker.js (msgpack → ImageBitmap decode, frame coalescing)
│   └── __tests__/            jest suites
├── docs/                     NDPC_DATA_PROTECTION_ANNEX.md, LASTMA proposal, capability review
├── dataconnect/, functions/  Firebase Data Connect schema + Cloud Functions (separate surface)
├── firebase.json, apphosting.yaml, firestore.rules, storage.rules   Firebase hosting config
├── ARCHITECTURE.md           deep, source-verified backend map (process tree, queues, data flow)
├── IMPROVEMENTS.md, suggestions.md
└── (model weights live in backend/models/ only: yolov8n.pt, pavement model)
```

## Backend architecture

### Process model

One uvicorn process (`app.main:app`) plus child processes spawned at startup
(`backend/app/services/feed_manager.py`, `inference_pool_manager.py`):

- **Main process** — FastAPI app, 18 routers, `ConnectionManager`, service registry
  (`services/services.py` initializes FeedManager, IncidentManager, AnalyticsService,
  TrafficSignal, Weather, Routing, Notification, VideoManager, etc.).
- **Inference pool** — `performance.inference_pool_size` (3) `multiprocessing.Process`
  workers (pool is pinned: `pin_inference_pool: true`), each running `core_module` per
  frame: detect → track → lane band → ReID → speed → anomalies. Models: YOLOv8
  (`models/yolov8n.pt`, TensorRT engine when available, `vehicle_detection.yolo_imgsz: 640`,
  conf 0.3, classes `[0,1,2,3,5,7]` — person/bicycle included for VRU analysis), ByteTrack-style
  tracker (`tracking.*`: Kalman via filterpy, appearance weight 0.5), ReID embedding
  (mobilenet_v3_small, 128-d, cosine threshold 0.70, Redis-synced gallery, TTL 14400s).
- **Ingestion workers** — one process per feed (1:1), decode frames into shared-memory
  segments (`utils/shared_frame_buffer.py`, pool `shm_pool_size: 4000` × 1.5 MB in
  /dev/shm) and enqueue handles.
- **Analytics worker** — buffers per-feed metrics, flushes batches to SQLite.
- **Database writer** — sole consumer of the `db_writes` Redis queue (circuit breaker,
  bounded re-enqueue, graceful drain).

All IPC queues are Redis-backed (db 0, `localhost:6379`, `redis.enabled: true`):
`shm_free_pool`, `worker_cmd_{worker_id}`, `central_output` (stream, consumer group
`output-readers`), `db_writes`, `feed_cmd_{feed_id}`, `analytics_input`/`analytics_output`,
`video_writer_queue`.

### Per-frame data flow

1. Ingestion worker decodes a frame → SHM segment → handle pushed to the feed's slot.
2. Inference worker (`core_module.detect_and_track`) detects, tracks, assigns a lane band
   (6 position-derived bands over the ROI, hysteresis 3 frames), matches/registers ReID
   global ids, estimates speed (homography calibration `vehicle_detection.calibration`
   + `pixels_per_meter: 30`; falls back to pixel velocity), runs OCR only if enabled
   (EasyOCR, optional Gemini; shipped disabled), and computes anomalies.
3. Results: `vehicle_data` → `db_writes` queue; a compact msgpack envelope
   `{f,i,ts,v,m,bg,ln}` (feed, frame index, timestamp, vehicles, metrics, background
   JPEG, lanes) → `central_output` stream.
4. `result_processor` reads the stream → `FeedBroadcaster` → `ConnectionManager`, which
   fans out to WebSocket clients with per-client HIGH/LOW priority queues, a per-feed
   rate gate, and RTT-adaptive payload tiers.
5. Frontend decodes binary msgpack in `public/workers/video-worker.js` (JPEG → ImageBitmap,
   latest-frame coalescing, monotonic frame index with loop-reset at 100).

### API surface

All routers are mounted under `/api/v1` (see `app/main.py` `include_router` calls):

| Prefix | Router | Main endpoints |
|---|---|---|
| `/api/v1/feeds` | feeds.py | `GET/POST /`, `GET /{id}`, `PATCH /{id}/config`, `DELETE /{id}`, `POST /{id}/start|stop|restart`, `POST /start-all`, `POST /stop-all`, `GET /sample-feed-data`, `GET /{id}/kpis`, `GET /{id}/metrics-history` |
| `/api/v1/incidents` | incidents.py | `GET/POST /`, `POST /{id}/acknowledge`, `POST /{id}/resolve`, `PATCH /{id}`, `GET /search/results`, evidence bundle endpoints `/{id}/evidence[/export|/verify|/release/verify]`, `GET /{id}/escalation` |
| `/api/v1/vehicles` | vehicles.py | `GET /`, `GET /tracks`, `GET /{license_plate}`, `GET /global/list`, `GET /global/{id}/history`, `GET /global/{id}/gallery`, ANPR `GET /plates/check`, `GET /plates/lists` |
| `/api/v1/analytics` | analysis.py | `GET /trends`, `POST /detect-anomalies`, `POST /predict-incident-likelihood`, `POST /predictions/location`, `GET /nodes/congestion`, `POST /analyze/*` (average_traffic, pattern, anomaly, time_series, rolling_averages), `GET /od-matrix[-roads]`, `GET /heatmap-roads`, `GET /travel-times`, `GET /history/{feed_id}`, `GET /emissions` |
| `/api/v1/alerts` | alerts.py | `GET /`, `DELETE /{id}`, `PATCH /{id}/acknowledge` |
| `/api/v1/video` | video.py | `GET /stream/{stream_id:path}`, `POST /record/start`, `POST /record/stop`, `GET /processed-videos`, `GET /processed-videos/{id}/download` |
| `/api/v1/routes` | routing.py | `POST /personalized`, `GET/POST /history`, `GET/POST /profile`, `GET /history/analytics`, `POST /suggestions/feedback`, `POST /optimize`, `GET /supported-areas`, `GET /work-zones` |
| `/api/v1/roads` | roads.py | `GET/POST /`, `POST /snap`, `GET /assets`, `GET/PATCH/DELETE /{road_id}`, `GET /{road_id}/feeds`, `GET /{road_id}/handoff` |
| `/api/v1/presets` | presets.py | `GET/POST /`, `GET/PATCH/DELETE /{preset_id}`, `GET /{preset_id}/feeds` |
| `/api/v1/signals` | signals.py | `GET /`, `POST /{signal_id}/set_phase` |
| `/api/v1/weather` | weather.py | 2 GET endpoints (service disabled by default: `weather_service.enabled: false`) |
| `/api/v1/events` | events.py | 2 GET endpoints |
| `/api/v1/logs` | logs.py | `GET /` (allowlisted log files only: `log_files.allowed_log_files`) |
| `/api/v1/config` | config.py | `GET /` |
| `/api/v1/pavement` | pavement.py | `POST /` (analyze), `GET /reports`, `GET /reports/{name}` |
| `/api/v1/webrtc` | webrtc.py | `POST /offer/{feed_id}` (aiortc signaling) |
| `/api/v1/websocket` | ws_monitoring.py | `GET /health` (connection stats) |
| `/api/v1` | ws.py | `WebSocket /ws/{client_id}` — control channel (subscribe/auth/ping/pong) + binary frame stream |

Plus root-level endpoints: `GET /`, `GET /health`, `GET /health/detailed` (db/redis/mongo/gpu
status), `GET /api/v1/snapshots/{file_path:path}` (path-traversal-guarded snapshot serving).

Interactive docs: `/docs` and `/redoc` (FastAPI defaults).

### Middleware stack (outer → inner)

`install_tunnel_auth` (fail-closed tunnel token gate, exempt only `/health`, `/docs`,
`/openapi.json`, `/redoc`) → `TunnelSecretMiddleware` (secret redaction + header→query
promotion) → `RateLimitMiddleware` (default 60 req/60s; stricter limits on
`/api/v1/analytics` 10/60 and `/api/v1/feeds` 30/60) → `LoggingMiddleware` →
`SecurityHeadersMiddleware` → `RequestIDMiddleware` → CORS (dev: anchored regex for
tunnel hosts + localhost; prod: explicit `ALLOWED_ORIGINS`/`cors.allowed_origins` list,
no regex). Error handlers re-apply the same CORS allowlist so 4xx/5xx aren't masked by
CORS errors. The tunnel password/token in query strings is scrubbed from uvicorn logs at
emit time (`TunnelSecretLogFilter`).

## Frontend (hosting/)

Next.js 16 (app router, `output: 'standalone'`) + React 19 + TypeScript + Tailwind CSS 3 +
Radix UI components + Leaflet maps + recharts. Security headers (CSP, HSTS, ...) are set
in `next.config.ts`.

Pages: `landing`, `login`/`signup` (Firebase Auth via `lib/auth/`), `dashboard`
(surveillance matrix + KPI cards + incident command center), `surveillance`,
`analytics/explore`, `anomalies`, `incidents`, `dashboard/map`, `routes`, `signals`,
`nodes` (corridor topology), `recordings`, `history`, `impacts`, `export`, `logs`,
`preferences`.

Data paths:

- **WebSocket**: `lib/websocket/WebSocketClient.ts` (singleton, 25s app-level ping / 100s
  pong timeout, token re-auth) connects to `NEXT_PUBLIC_WS_URL` → `/api/v1/ws/{client_id}`.
  Binary msgpack frames are decoded off-thread in `public/workers/video-worker.js`.
- **REST**: `lib/api/APIClient.ts` calls same-origin `/api/v1/*`, which the Next route
  handler `app/api/v1/[[...path]]/route.ts` reverse-proxies to the backend, re-reading
  `.env*` per request (no restart needed when the tunnel URL changes). Hop-by-hop headers
  are stripped; the loca.lt `?password=` is appended only for loca.lt hosts.

## Configuration

### Backend — `backend/configs/config.yaml`

Single source of tunables (loaded by `app/config.py`; there is **no .env loader** in the
backend). Important sections: `database` (SQLite path `backend/data/vehicle.db`, optional
TimescaleDB/Mongo disabled), `redis`, `inference` / `vehicle_detection` (model, thresholds,
ReID), `tracking`, `performance` (pool size, SHM pool, skip valve), `video_processing` /
`video_output` (target fps 5, stream 640×480), `safety_analytics` (wrong-way gates),
`behavior_analysis` (hard-braking), `incident_management` (debounce/snapshot caps),
`retention` (7-day age cap, 1.5 GB size cap, 90-day audit), `evidence` (HMAC seal),
`privacy` (blur on export), `logging` (per-logger file routing), `post_startup_processing`
(3 looped sample feeds auto-registered), `websocket` (max 1000 conns, ping 25s/pong 100s),
`cors`. Feature flags in `configs/features.yaml`: `route_optimization`,
`personalized_routing`, `advanced_analytics` — all disabled.

### Environment variables

Backend (must be in the serving process's environment — no dotenv):

| Var | Purpose |
|---|---|
| `TUNNEL_AUTH_TOKEN_FILE` | Absolute path to a file containing the tunnel auth token (preferred; `backend/keys/tunnel_auth.token`). Inline `TUNNEL_AUTH_TOKEN` also works. |
| `TUNNEL_AUTH_REQUIRED=0` | Disable the tunnel gate (local dev only; default fail-closed). |
| `ROUTE_ONE_EVIDENCE_KEY` / `ROUTE_ONE_EVIDENCE_KEY_FILE` | HMAC key for sealing evidence bundles. |
| `ENVIRONMENT` | `development` enables the tunnel-host CORS regex; anything else uses the explicit origin list. |
| `ALLOWED_ORIGINS` | Comma-separated production CORS origins. |

See `backend/tunnel_token.env.example` and `backend/evidence_key.env.example` for the full
checklists (absolute paths required; secrets must match between backend and frontend).

Frontend — `hosting/.env.local` (all `NEXT_PUBLIC_*`, inlined at build time; restart
`npm run dev` after changing): `NEXT_PUBLIC_FIREBASE_*` (api key, auth domain, project id,
storage bucket, messaging sender id, app id, measurement id), `NEXT_PUBLIC_API_BASE_URL`,
`NEXT_PUBLIC_WS_URL`, `NEXT_PUBLIC_LOCALTUNNEL_PASSWORD`, `NEXT_PUBLIC_TUNNEL_AUTH_TOKEN`
(must equal the backend token file's contents).

## Running

Prerequisites: Python 3.10+, Node.js 20+, Redis running on `localhost:6379`, a GPU with
CUDA is used when present (health endpoint reports it) but not required to boot.

```bash
# Backend (from the repo root or backend/)
cd backend
pip install -r requirements.txt          # on CPU-only boxes install torch with --index-url first
python -m app.main                       # uvicorn on 0.0.0.0:8000, reload=False, WS protocol keepalive OFF

# CLI equivalent — the flags are MANDATORY or tunnelled WebSockets die at ~40s:
uvicorn app.main:app --port 8000 --ws-ping-interval null --ws-ping-timeout null --timeout-graceful-shutdown 25
# Never pass --reload: it watches backend/logs/ and a reload kills all GPU workers and feeds.

# Frontend
cd hosting
npm install
npm run dev                              # http://localhost:3000
```

Root `package.json` wraps the frontend: `npm run dev|build|start|lint|test` (all run in
`hosting/`). `npm run backend:dev` starts the backend (note: it passes `--reload`, which
is harmful — prefer `python -m app.main`).

Deployed operation (as run in production today): the backend runs on a Kaggle box at
`/kaggle/working/R1v0.1`, exposed through a loca.lt tunnel. Deploy = `git push` to origin,
then `git pull` **and restart the backend process** on the box (a pull alone does not
deploy). Deps there are installed with
`uv pip install --python .venv/bin/python -r backend/requirements.txt`.

## Testing

```bash
cd backend && pytest                     # backend/tests/ + backend/app/tests/
cd hosting && npm test                   # jest --coverage, suites in hosting/__tests__/
cd hosting && npm run lint               # eslint
```

## Storage & data

- SQLite `backend/data/vehicle.db` — vehicles, incidents, alerts, processed_videos,
  route_history, user_profiles, prediction_logs, audit trail, etc.
- Redis db 0 — queues/streams (above), ReID gallery + `reid:sync` pub/sub.
- `backend/data/snapshots/` — incident JPEGs (severity-gated, rate-capped).
- `backend/data/evidence/` — sealed evidence bundles (HMAC manifest; `sealed:false` if no
  key is configured, and verification fails loudly on unsealed bundles).
- `backend/data/processed_videos/`, `pavement_images/`, `pavement_reports/` — all under the
  retention caps (`retention:` in config.yaml).
- MongoDB and TimescaleDB exist in config but are disabled (`mongodb.enabled: false`,
  `database.timescaledb.enabled: false`).

## Pointers

- `ARCHITECTURE.md` — deep source-verified map of the backend process tree, queue
  topology, per-frame flow, and live/dead module census.
- `docs/` — data-protection annex (NDPA) and LASTMA proposal; `hosting/docs/` — frontend
  implementation plan.
- `backend/tunnel_token.env.example`, `backend/evidence_key.env.example` — secret setup
  checklists. `backend/keys/` and `*.token`/`*.key` are gitignored.

## License

MIT.
