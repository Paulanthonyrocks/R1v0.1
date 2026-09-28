# R1v0.1 Backend Architecture — Deep Map

Source-verified 2026-09-24. Deploys to Kaggle via commit→push→pull+restart; local copy is /home/user/R1v0.1, live copy /kaggle/working/R1v0.1. Data/ on local is stale (Sep 9) — logs sync, data doesn't.

## Process Tree (runtime composition)

- **Main (uvicorn → app/main.py lifespan → app/services/services.py)**
  - FastAPI app, 16 routers (feeds, incidents, vehicles, ws, video, routing, signals, alerts, analysis, config, events, logs, weather, webrtc, ws_monitoring)
  - ConnectionManager init: max_connections=1000, ping_interval=25, pong_timeout=100
  - services.py initializes 14 services in dependency order: TrafficSignal → Notification → Weather → Incident → Analytics → Node → FeedManager → VideoManager → RouteOptimization → PersonalizedRouting → AdvancedAnalytics
  - FeedManager owns: RedisQueue 'db_writes', RedisStreamQueue 'central_output', InferencePoolManager, FeedBroadcaster, per-feed process registry, 5s KPI loop, sample-feed manager, DatabaseWriter process (as of 2026-09-24)
- **Inference Pool** (multiprocessing.Process ×3, spawned by InferencePoolManager :155): each runs core_module per-frame pipeline (detection → tracking → ReID → OCR → vehicle_data dict → db_queue :1202, compact msgpack → 'central_output' stream)
- **Analytics Worker** (Process, feed_manager :1841): buffers metrics → 100-row flushes to location_metrics (~18s cadence at 5.4 rows/s)
- **Database Writer** (Process, feed_manager :438, wired 2026-09-24 — previously unwired since Apr): sole consumer of 'db_writes' RedisQueue; circuit-breaker (50 consecutive failures), bounded re-enqueue (_MAX_REENQUEUE), prune thread, 5s graceful drain
- **Feed Ingestion** (Process ×N, 1:1 with feeds, load-balanced ×4/5 in live run): frame → SHM free_pool → inference input

## Queue/Stream Topology (all Redis-backed, multiprocessing-safe)

| Queue | Type | Producer → Consumer |
|---|---|---|
| `shm_free_pool` | RedisQueue | shared_frame_buffer:73 — frame buffer recycling |
| `worker_cmd_{worker_id}` | RedisQueue(100) | pool_manager:144 → inference worker control |
| `central_output` | RedisStreamQueue(group=output-readers) | inference_worker:347 → result_processor |
| `db_writes` | RedisQueue(DB_QUEUE_MAXSIZE) | feed_manager:136 → **DatabaseWriter** (was inline reader before 2026-09-24) |
| `feed_cmd_{feed_id}` | RedisQueue(50) | feed_manager:1385 → ingestion_worker:425 |
| `analytics_input` / `analytics_output` | RedisQueue | feed_manager:201-202 → analytics_worker |
| `video_writer_queue` | RedisQueue | feed_manager:1389 → ingestion_worker:192 |

## Per-Frame Data Flow (verified)

1. `ingestion_worker` decodes frame → SHM segment, pushes handle to pool
2. `inference_worker.core_module`: detect → track (ByteTrack) → ReID (budget_gated :1328) → OCR if plate → produces `vehicle_data` dict + serialized `tracked_vehicles`
3. `vehicle_data` → `db_queues` db_queue (feed_manager RedisQueue 'db_writes')
4. `central_output` stream → `result_processor` (msgpack packs {f(feed),i(idx),ts,v(vehicles),m(metrics),bg(frame),ln(lanes)} :563-568) → `FeedBroadcaster` → `ConnectionManager`
5. `ConnectionManager` per-client dual-queue (HIGH PriorityQueue + LOW deque + signal wake); tunnel rate gate (0.25s/feed, 2026-09-24); adaptive full/small payload by RTT :906+
6. `/ws` router (auth + expiry-watcher, fix at :155) → frontend WebSocketClient (frame heartbeat every 300 :236; ping/pong; token re-auth)
7. DB: `save_vehicle_data_batch` (threading.Lock-serialized, 4-attempt tenacity), AnalyticsService 100-row flush, ReID `save_reid_identity` + Redis reid:sync pub/sub

## Services: wired vs wiring-complete vs dormant

- **Fully live + heavily touched 2026-09-24**: FeedManager (writer spawn, bounded shutdown, F2), ConnectionManager (rate cap 0.5→0.25, batched drain, deque 90→180, +40s-kill attribution), reid_manager (F-live warm start, F3 dim guard, owner-gated save), routers/ws (auth_exp_holder), analytics_worker/service (live, 21801 rows flushed in last run)
- **Wired, live, untouched**: IncidentManager (KPI counts via _get_active_incidents_count; cached 10s at feed_manager:2089), VehicleDataCache, PredictionScheduler, NodeManager (:3 refs downstream but live)
- **Initialized + referenced downstream, never observed in this session's logs**: TrafficSignal/Notification/Weather/RouteOptimization/PersonalizedRouting/AdvancedAnalytics/VideoManager — traffic lighter than feeds/KPIs; alive per services.py but no live-session evidence yet. VideoManager recording path uses the db_writer-adjacent result_processor pump (:459 feed_manager).

## Dead & Unwired — what the census actually found

Verified via importer-count + relative-import re-check (first grep pass missed `.module` relative imports; test_cache/detection/transforms all have live importers).

**DEAD (zero importers, zero string-spawn, zero config key):**
- `app/core/test_cache.py` — 39-byte `TEST_MARKER_123` print stub, Apr 19. Pure scrap.
- `app/ml/car_classifier.py` — no importers; only referenced in a comment in reid_model.py describing its checkpoint format (training tool lineage). Not imported live.
- `app/ml/train_reid.py` — training script, no runtime importers. Only usage is its own `__main__`.
- `app/utils/encryption.py` (46 lines, secrets_manager wrapper) — zero importers.
- `app/utils/idempotency.py` (44 lines, FastAPI Header helper) — zero importers.
- `app/utils/resilience.py` (60 lines, tenacity wrapper) — zero importers.

**UNWIRED-UNTIL-2026-09-24, NOW WIRED:**
- `app/core/database_writer.py` — created Apr 9 (aa7d492), 7 touching commits latest Sep 18, NEVER called until wired into feed_manager startup today. Now the sole 'db_writes' consumer with 3 bugs fixed (async close, stale stop event, drain feed_metrics gap).

**F4-DELETED 2026-09-24 (grep-verified before AND after):**
- `app/ml/reid_manager.py` (106-line dead class)
- config keys `reid_interval_frames` (:1010), `min_track_age_for_appearance` (:978)
- `services/reid_manager.match_only()` (zero call sites)

**Dead-adjacent observed today:**
- `_read_db_queue` inline reader — now fallback-only; kept because writer spawn can fail.
- adaptive_streaming small_bg producer — wired downstream (result_processor :554-568 + connection_manager :906) but no producer writes `extra["bg"]`; config `adaptive_streaming: false` makes it moot. Wiring it = next tier if 4fps/feed is still coarse.

## What Was NOT Traced (would need another dive)

Routing optimization internals, TrafficSignalService side effects (does it write signals back or just model?), VideoManager recording lifecycle end-to-end, node_manager role beyond registration, AnalyticsService Pro (analytics_service_pro.py — never seen imported), dataconnect/functions/ at repo root (Firebase functions — separate deploy surface), yolov8n.pt at ROOT (not backend/models — check if it's a duplicate of the model the backend actually loads).

## In-flight changes awaiting live verification (2026-09-24, uncommitted at write time)

1. Frame delivery: tunnel rate cap 0.5→0.25s/feed; sender LOW drain batched ×4; tunnel deque 90→180
2. DatabaseWriter wired (feed_manager:438) + idempotence guard
3. Bounded shutdown (per-stage wait_for, broadcast=False teardown) + uvicorn timeout_graceful_shutdown=25
4. WS: auth_exp_holder expiry watcher (17:26 token-kill fix), ws_ping None/None (uvicorn protocol keepalive off)
5. ReID: F-live (worker DB warm start), F1 (worker shutdown), F2 (feed_manager skip — later found inert), F3 (dim guard), F4 (dead code deleted)

Live A/B for #1/#2/#3/#5: **next restart's logs** — expect `Started DatabaseWriter process`, `ReID state loaded from Database. Total IDs: N` (N>0 if registrations persisted), clean shutdown (all stages log themselves), console.log heartbeats ~15000/feed over the hour.
