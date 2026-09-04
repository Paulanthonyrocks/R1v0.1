# R1v0.1 Improvement Backlog

Living list. Checked = shipped + verified. Open = agreed, not yet done.
Updated 2026-09-04. Evidence pointers are `path:line`-ish; verify before acting.

## Done (live-proof outstanding where noted)

- [x] Wrong-way lane gate + flow-log throttle (`app/services/safety_monitor.py`)
- [x] Hard-brake threshold 6 -> 8 (`behavior_analysis.accel_threshold_mps2`)
- [x] ReID gallery atomic save, pid-unique tmp (`app/services/reid_manager.py`)
- [x] Tunnel password redaction in WS logs + jest test (`hosting/lib/api/backendBaseUrl.ts`)
- [x] WS frame heartbeat sampled 1/300 (`hosting/lib/websocket/WebSocketClient.ts`)
- [x] Signals honesty: paths, `get_all_signal_states`, str/enum phases, ACCEPTED member, [] + 503 unconfigured
- [x] Incidents PATCH undefined `db` fixed
- [x] Predictive sidebar computed from live series; AIInsightsPanel drops signal prescription
- [x] Retention watches `backend/data/snapshots`
- [x] RouteHistoryPanel path, optimize 501, supported-areas []
- [x] Weather/EventService gated on config; node broadcast default False
- [x] batch_size 8 -> 2; per-detect + feed_manager/connection_manager logs demoted
- [x] 6 dead config keys removed; prediction_scheduler default aligned False
- [x] Lane bands 0..2 from ROI x-range (`lane_bands`, `CoreModule._assign_lane_band`)

## Open — in ROI order

1. TRT engine build (THEIR T4 step): `backend/.venv/bin/python backend/scripts/export_tensorrt.py`
   on Kaggle after pull. Re-run after ANY yolo_imgsz change. Never commit the .engine.
2. Lane verification (next live run): expect lane=0/1/2 on wire, per-band consensuses,
   wrong-way judging active per band. If one band mixes directions -> retune bands/threshold.
   If boundary flicker shows -> add hysteresis (per-track vote state).
3. Snapshot severity-gating: retention bounds at 7d, but storms still write per incident.
   Gate `request_snapshot` on high/critical.
4. SHM pool 6000x1.5MB (~9GB, ~99% free): drop to 2000, re-tune skip_threshold 400-600.
5. ReID sync cost: pipeline Redis reads in `_sync_from_redis`, list+periodic-vstack gallery.
   Optional: single-owner pickle save (DB already source of truth).
6. Unreachable backend fiction (no UI, no urgency): proactive canned suggestions
   (`personalized_routing_service.py:304`), SimulationService dead code (remove or wire).
7. Providers to wire (project-scale, not tasks): real signal controller URL, road network
   for optimize, event/weather api_urls. Until then: 501/[]/skipped honesty holds.
8. Parked correctly, revisit with new inputs: OCR/Gemini flip (needs legible plates),
   anomaly model, file_watcher, TimescaleDB/Mongo.
9. Test gaps: signals router/service, incidents PATCH, weather/events skip paths,
   supported-areas [], predictive MAPE math.
10. Minor: alerts RBAC TODOs; node_congestion_broadcast now fixed, watch for recurrence
    of top-level-vs-nested flag reads.

## Standing deploy recipe (Kaggle)

git pull + RESTART backend (pull alone does not deploy). Hosting rebuild for frontend changes.
