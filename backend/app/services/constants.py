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

    # Cleanup
    ATEXIT_JOIN_TIMEOUT = 0.5
    TERMINATE_JOIN_TIMEOUT = 1.0
