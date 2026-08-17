from typing import Dict, Any, Optional, List

class FeedManagerConstants:
    # Process & Queue Defaults
    PROCESS_JOIN_TIMEOUT = 3.0
    QUEUE_MAX_SIZE = 500
    QUEUE_DRAIN_LIMIT = 100
    MAX_METRICS_HISTORY_LENGTH = 1000

    # Scaling & Slots
    # SLOT_COUNT 24 -> 6 (2026-08-17). The 24 value enforced a strict 1:1
    # worker:slot mapping (pool 24 == slots 24), which meant EACH FEED WAS
    # SERVED BY EXACTLY ONE WORKER -- with 3-5 feeds, 19-21 of 24 workers sat
    # idle (confirmed: only Workers 0/1/2 logged per-feed METRICS in the
    # 08-17 00:00 run) and each feed's fps was capped by a single worker's
    # ReID+detect cost. With the rewritten scale_pool (unowned-slot-first +
    # step-4 respawn gate) the old overwrite bug that motivated 24 is gone,
    # and slot_count=6 + pool 24 fans out to 4 consumers per slot
    # (wids {0,6,12,18} on slot 0, etc.) -- verified by driving the REAL
    # scale_pool: every slot gets 4 consumers, no orphaned spawns, feeds
    # route to slots (wid % 6) = 0..4.
    #
    # CAVEAT: oscillating scale_pool calls (scale-down then up) under
    # pool_size > slot_count strand the low-wid survivors (their slots get
    # stolen and the respawn is refused). pin_inference_pool: true keeps the
    # scaling monitor from calling scale_pool after boot, so live the only
    # call is the boot 0->24 -- the safe path. If you ever unpin, keep
    # slot_count >= max_concurrent_feeds or re-test the oscillation case.
    SLOT_COUNT = 6
    MIN_WORKERS = 1
    MAX_WORKERS = 24
    IDEAL_WORKERS = 4  # Balanced for 2-GPU systems
    SCALE_UP_THRESHOLD = 10
    SCALE_DOWN_THRESHOLD = 1
    SCALE_COOLDOWN = 30

    # Timing & Intervals
    KPI_BROADCAST_INTERVAL_DEFAULT = 5.0

    MIN_READ_DELAY_MS_DEFAULT = 1
    MAX_READ_DELAY_MS_DEFAULT = 100
    DELAY_ADJUSTMENT_FACTOR_DEFAULT = 1.1
    QUEUE_LOG_INTERVAL_DEFAULT = 15.0
    METRICS_WINDOW_DEFAULT = 10

    # DB Queue
    DB_QUEUE_MAXSIZE = 100000
    DB_BATCH_SIZE = 5000
    DB_IDLE_SLEEP = 0.05
    DB_FAST_SLEEP = 0.001
    DB_SLOW_SLEEP = 0.005

    # Worker & Process Control
    WORKER_SPAWN_LIVENESS_CHECK = 0.5
    FEED_CMD_QUEUE_MAXSIZE = 50
    RESTART_GRACE_PERIOD = 2.0

    # Result Processing
    # NOTE: was 200. Each loop pulls up to RESULT_BATCH_SIZE items from
    # central_output, but the dedup in result_processor keeps only
    # latest_per_feed (~3 for 3 feeds). So a 200-item batch wastes
    # ~197 SHM read+release cycles on frames that are discarded, inflating
    # reader latency. That latency is what lets a segment name get queued
    # twice (writer recycles it to another feed before the lagging reader
    # consumes the first ref) -> read()'s expected_feed_id hash mismatch
    # -> the "HIGH SHM FAILURE RATE" climb seen in logs. 32 keeps a
    # small burst buffer above feeds*slots while keeping the reader close
    # to real-time so segments don't recycle under it.
    RESULT_BATCH_SIZE = 32
    RESULT_READER_IDLE_SLEEP = 0.001
    RESULT_READER_FAST_SLEEP = 0.01
    RESULT_READER_SLOW_SLEEP = 0.02
    KEYFRAME_INTERVAL = 30

    # Watchdog & Recovery
    WATCHDOG_INTERVAL = 5.0
    MAX_RESTART_ATTEMPTS = 5
    BACKOFF_BASE_DELAY = 5
    BACKOFF_MAX_DELAY = 3600
    # Inference workers exit with this code on a CUDA-fatal error (poisoned
    # device context; see inference_worker._exit_worker_fatal). The watchdog
    # counts these deaths per worker id and quarantines the worker -- and
    # halts its feeds -- after WORKER_QUARANTINE_THRESHOLD of them. A device
    # that faults on its first compute kernel in a FRESH process (observed on
    # cuda:1, 2026-08-16: boot load OK, warmup illegal-address, 26 respawns,
    # zero recovery) is a hardware failure; respawning cannot fix it, and the
    # dead worker's slot queue keeps accumulating frames until the SHM free
    # pool exhausts and healthy feeds start dropping.
    CUDA_FATAL_EXIT_CODE = 42
    WORKER_QUARANTINE_THRESHOLD = 3

    # Cleanup
    ATEXIT_JOIN_TIMEOUT = 0.5
    TERMINATE_JOIN_TIMEOUT = 1.0
