# R1v0.1 Improvement Backlog

Living list. Checked = shipped + verified. Open = agreed, not yet done.
Updated 2026-09-15. Evidence pointers are `path:line`-ish; verify before acting.

## Done (live-proof outstanding where noted)

- [x] Wrong-way lane gate + flow-log throttle (`app/services/safety_monitor.py`)
- [x] Hard-brake threshold 6 -> 8 (`behavior_analysis.accel_threshold_mps2`)
- [x] ReID gallery atomic save, pid-unique tmp (`app/services/reid_manager.py`)
- [x] Tunnel password redaction in WS logs + jest test (`hosting/lib/api/backendBaseUrl.ts`)
- [x] WS frame heartbeat sampled 1/300 (`hosting/lib/websocket/WebSocketClient.ts`)
- [x] Signals honesty: paths, `get_all_signal_states`, str/enum phases, ACCEPTED member, [] + 503 unconfigured
- [x] V2X incident broadcast wired (`incident_manager.py` 6b -> `V2XService.broadcast_directive`,
  HIGH/CRITICAL only, type->directive map; disabled by default `v2x.enabled False`).
  Live-proof pending next run (enable + UDP listener).
- [x] Incidents PATCH undefined `db` fixed
- [x] Predictive sidebar computed from live series; AIInsightsPanel drops signal prescription
- [x] Retention watches `backend/data/snapshots`
- [x] RouteHistoryPanel path, optimize 501, supported-areas []
- [x] Weather/EventService gated on config; node broadcast default False
- [x] batch_size 8 -> 2; per-detect + feed_manager/connection_manager logs demoted
- [x] 6 dead config keys removed; prediction_scheduler default aligned False
- [x] Lane bands 0..2 from ROI x-range (`lane_bands`, `CoreModule._assign_lane_band`)
- [x] Sep-05/07 wrong-way hardening: max-speed ceiling 250px/s (ID-switch teleports),
-   confirm-frames 3 consecutive, cooldown on emission only (`safety_monitor.py`);
-   two-way per-band verdict + fast-attack/bounded-release streak (`lane_calibrator.py`);
-   lane id in WRONG_WAY log. Commits 763463e, 0367ca0, 97b9201.
- [x] Snapshot storm gated three-deep: per-vehicle cooldown 300s + per-feed cap
-   6/min + severity floor HIGH (`snapshot_min_severity`, `incident_manager.py` step 7).
- [x] SHM visibility + log hygiene: global-acquired/rel_set/missing in SHM-STATS,
-   pressure on free-fraction first (6000-slot pool kept deliberately, Sep-07 acq~2600
-   healthy); PONG race demoted to debug, feed-history error->warn, WS reconnect
-   notify-once (`feed_manager.py`, `ws.py`, `dashboard/page.tsx`, `WebSocketClient.ts`). Commits 7342acd, c8e87bc.

## Open — in ROI order

1. Inference throughput (Sep-09 38-min run, root-caused from backend_main.log):
   TRT engine WAS built and loaded on all 4 workers (13:10:57, "TensorRT engine
   loaded successfully") — "TRT unbuilt" was stale. Engine REBUILT Sep-09 17:20
   (export output verified: FP16, imgsz 960, input (1,3,960,960), 138.9MB,
   clean build) — BUT SAME RECIPE batch=1, so throughput unchanged; the rebuild
   alone does not move fps. The bottleneck is structural: static-batch-1 TRT
   engine forces per-frame inference (inference_worker.py clamps batch_size=1
   when is_trt_engine, so the batch 8->2 config is bypassed under TRT), each
   forward is a 960x960 fp16 pass. Per-feed worker cost differs by scene:
   W0/Feed_1 lifetime 3.25 fps, W1/Feed_2 3.46, W2/Feed_3 4.98 — yet W2 SHARES
   GPU0 with W0, so GPU contention is ruled out; Feed_1's scene (most
   vehicles/tracks) is the cost. GPU split: W0+W2 -> GPU0, W1+idle W3 -> GPU1.
   KEY IMPLICATION: a T4 does a 960 fp16 yolov8n forward in ~10-15ms; at
   3.25 fps (~310ms/frame) the GPU forward is ~5% of per-frame cost — the
   limiter is CPU-side (decode/tracking/ReID/serialize), so a bigger batch
   engine alone won't fix it either.
   Consequence: workers deliver ~6.7-11.7 fps combined vs 15 ingested -> SHM
   backlog grows all run (free 98.8% -> 49% by 13:29, acq ~3050) until slot
   queues cap, then drops: W0 1298, W1 841 (both "SHM recycled" class), W2 0.
   Note the fps "sag to 2.3" is the accumulation onset, not decay: fps was
   ~3.5-3.9 from minute 1 on W0; drops start 13:39 when the backlog saturates.
   SHIPPED instrumentation (Sep-09): (i) frames_dropped split by cause in
   METRICS — drops_shm_recycled (worker slow), drops_output_full
   (result-processor slow), drops_other (ingestion pressure/shed) —
   worker_utils.py WorkerMetrics + all 8 increment sites (inference_worker
   261/862/1407, ingestion_worker 462/505/592/616/659) + added to
   result_processor _NON_SMOOTHED_METRIC_KEYS (monotonic; EMA would corrupt);
   (ii) per-stage timing: STAGE-TIMINGS INFO line every 30s per worker
   (shm_read_decode / batch_infer / track_post / reid / forward, ms/frame) —
   the next run attributes the ~300ms/frame directly instead of guessing.
   Also: incident log lines now carry feed id (incident_manager.py:234).
   ROI fixes: (a) re-export engine with dynamic batch axis + raise
   inference.batch_size — but see KEY IMPLICATION, GPU forward is not the
   limiter; (b) feed cost is scene-driven, slot/worker rebalancing gains
   little; (c) read the new STAGE-TIMINGS + drop-split from the next run
   BEFORE any further tuning. Never commit the .engine (.gitignore now
   covers *.engine/*.onnx — it did NOT before, and a pushed 139MB engine
   would break the auto-commit deploy loop).
   Sep-15 68-min run closes the loop: batch_infer 13-18ms (GPU forward ~5%,
   KEY IMPLICATION confirmed), track_post 30-54ms dominant (finalize ~12-16 +
   reid_assoc ~13-20 per TRACK-SUBSTAGES). Lifetime fps 5.0 flat all workers,
   0 drops in all classes, SHM free 100% all run. Closure is conditional on
   5fps ingest — ceiling above that untested; re-check the drop split first.
2. Lane verification (Sep-09 4h live run: VERIFIED working as designed): bands 0..5
   all calibrate on all 3 feeds (conf 0.8-0.99), per-band judging active, two-way
   suppression fired 24 skips with 0 wrong-way flags and 0 false storms (vs 324
   ID-switch flags Sep-07 — confirm-3 + 250px ceiling + verdict hold).
   Streak cap holds at 800. SHM 99->54->54% free, 0 drops, 0 pressure warnings
   (6000-slot call validated, acq~2756). ReID 13871 matches / 20 regs. 0 errors
   live (the 97 audit + 8 unhandled + Redis-refused lines in the file are local
   pytest noise appended 09:00-09:16, not Kaggle).
   Remaining geometry work: per-track vote hysteresis SHIPPED
   (`lane_bands.hysteresis_frames: 3`, `CoreModule._assign_lane_band` + vote
   pruning; single-frame flicker never reaches track["lane"], occlusion keeps
   committed lane). 8 unit tests green. Live-proof DONE Sep-15 68-min run:
   0 wrong-way flags.
   Second confirmation (Sep-09 38-min run, 13:10-13:49, clean SIGINT shutdown):
   0 ERROR lines, 0 WRONG_WAY incidents, 66 two_way=True flow verdicts
   (conf 0.81-1.00), streak cap holding at 800 with bounded release observed
   (800 -> 373), flow-log throttle at 115 sampled lines.
   Decel note: RESOLVED — not a bug. Sep-09 38-min run: 12 incidents on two
   clock series, ~301s apart (13:14:19 +5:01 steps of -10.6; 13:32:36 +5:01 of
   -12.2; one -10.2). Feed start 13:11:02 -> first hits at +197s / +1294s = 300s
   video-loop offsets. The sample videos are ~300s long and LOOP: the same two
   braking scenes replay every pass with new track ids (10s per-vehicle cooldown
   can't dedupe across loops), deterministic frames -> identical quantized
   accel values on a clock. Real braking never repeats identical values on a
   5-min clock; looping footage does. Confirmed both series' offsets mod 300s
   drift only +0.1s/pass (197.3, 198.5, 199.5... and 94.4, 94.8, 95.8...) —
   a code timer would hold constant; a loop drifts with pipeline jitter.
   No code change needed. On real cameras this signature disappears.
   Re-confirmed Sep-15 68-min run: 31 sudden-decel (Feed_1 x15 + Feed_2 x16,
   Feed_3 x0, the only real incidents), ~5:01 alternating clock, repeating
   quantized values — same loop signature. Lane side same run: 0 WRONG_WAY
   over 207 flow lines, lanes 0-5 all live (22/59/52/18/38/18), streak max
   39/800, all verdicts two_way=False (suppression path unexercised — no
   two-way scenes this run).
- [x] Snapshot severity-gating: `request_snapshot` fires on high/critical only
-   (`incident_management.snapshot_min_severity: HIGH`, `incident_manager.py` step 7).
-   Cooldown (300s/vehicle) + cap (6/feed/min) underneath. Live-proof DONE
   Sep-15: 31 requested + 31 saved, 1:1 with decel incidents, no storm.
4. SHM pool: 6000 -> 4000 Sep-10 (config.yaml comment: steady-state acq ~3030
   left half the pool standing headroom; frees ~3GB tmpfs; tripwire = revert
   to 4800 if free < 25% or skip-decode latches). Sep-15 tripwire PASSED: free
   100% all run, orphan/missing/drops 0. acq 0-2 (gbl) is REAL, not a counter
   bug — SHM-STATS reads Redis SET sizes cross-process (`feed_manager.py:1766`);
   workers genuinely hold ~nothing at 5fps ingest (no backlog; the ~2600-3050
   era was pre decode-shed + pre reid fast-path). Revisit only with new pressure data.
5. ReID sync cost: `_sync_from_redis` now ONE pipeline round-trip + single
   batch vstack + single lock hold (was 2N round-trips + vstack-per-id, O(N^2)).
   Per-id decode guarded (corrupt emb skipped, rest still merge); missing-data
   ids re-pull cheaply each interval, same as before. Live-proof DONE Sep-15:
   reid stage 0.5-3.3ms/frame, gallery 39-53, ~22k matches, no stall.
   Still open (optional): single-owner pickle save (DB already source of truth).
- [x] Unreachable-backend fiction cleared: SimulationService deleted (dead, zero
-   inbound refs verified); proactive suggestion docstring honest (frequency nudge,
-   not traffic-aware — impl itself is real DB-backed code).
- [x] Alerts RBAC TODOs removed (both endpoints already enforce get_current_admin).
- [x] Alert endpoints un-broken: delete/ack/get_by_id were 500s (methods missing on
-   DatabaseManager) — implemented + alerts table widened to the schema the code
-   already assumed + migration backfills legacy narrow DBs on restart.
- [x] Signal set_phase success path un-broken: broadcast used nonexistent
-   SIGNAL_STATE_UPDATE enum (accepted commands returned ERROR) → SIGNAL_UPDATE.
- [x] Test gaps closed: signals router+service, incidents PATCH, weather/events skip
-   paths, supported-areas [] + optimize 501, predictive MAPE (extracted pure fn +
-   jest). 29 backend + 10 jest green here. Adjacent pre-existing, left: agent_core
-   circular import, test_dependencies stale import, personalized WithDb tz-naive
-   datetimes (outside the listed gaps; personalized test file at least collects now).
7. Providers to wire (project-scale, not tasks): real signal controller URL, road network
   for optimize, event/weather api_urls. Until then: 501/[]/skipped honesty holds.
8. Parked correctly, revisit with new inputs: OCR/Gemini flip (needs legible plates),
   anomaly model, file_watcher, TimescaleDB/Mongo.
9. Test gaps: closed (see Done). test_dependencies.py removed (tested a dead
   helper, zero surviving refs). Remaining suite debt is outside the listed gaps
   (agent_core circular import, personalized WithDb tz-naive).
10. Minor: alerts RBAC TODOs removed (done above); node_congestion_broadcast now fixed,
    watch for recurrence of top-level-vs-nested flag reads.

## Standing deploy recipe (Kaggle)

git pull + RESTART backend (pull alone does not deploy). Hosting rebuild for frontend changes.
