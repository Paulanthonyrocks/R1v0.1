from typing import Dict, Any, Optional, List

class FeedManagerConstants:
    # Process & Queue Defaults
    PROCESS_JOIN_TIMEOUT = 3.0
    QUEUE_MAX_SIZE = 500
    QUEUE_DRAIN_LIMIT = 100
    MAX_METRICS_HISTORY_LENGTH = 1000

    # Scaling & Slots
    # SLOT_COUNT raised 4 -> 24. With inference_pool_size = 24 (config.yaml),
    # slot_count < pool_size caused InferencePoolManager.scale_pool's
    # modulo-based assignment to OVERWRITE a worker's slot mid-spawn -- so
    # workers 0..3 logged "Slots assigned: [0..3]" at boot but the slot map
    # was immediately rewritten to wid=4..7, leaving workers 0..3 with stale
    # slot ownership and no incoming frames. Setting slot_count == pool_size
    # collapses effective_workers = min(target_size, slot_count) == pool_size,
    # so every wid gets exactly one distinct slot and no overwrite is possible.
    # Memory cost: ~24 RedisStreamQueue instances vs 4, ~negligible.
    #
    # INVARIANT: SLOT_COUNT >= MAX_WORKERS. Verified empirically in
    # investigation #2 (test_scale_pool_leak.py, scenario "Cold-start at max"):
    # pool_size == slot_count yields 1:1 slot ownership with no orphans.
    # DO NOT lower SLOT_COUNT without re-running the scale_pool integration
    # tests -- oscillating scale_pool calls under pool_size > slot_count
    # cause step 4 to leave workers stranded with stale slot ownership.
    # If you must lower it, keep pin_inference_pool: true so scale_pool's
    # early-return at line 102 keeps the bug unreachable.
    SLOT_COUNT = 24
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
