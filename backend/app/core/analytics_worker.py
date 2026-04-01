import os
import logging
import time
import queue
import signal
from typing import Dict, Any, List, Optional
from multiprocessing import Queue as MPQueue, Event

from ..utils.monitoring import TrafficMonitor
from ..services.safety_monitor import SafetyMonitor

logger = logging.getLogger("app.core.analytics_worker")

class AnalyticsWorker:
    def __init__(
        self,
        worker_id: int,
        config: Dict[str, Any],
        input_queue: MPQueue,
        output_queue: MPQueue,
        db_queue: MPQueue,
        stop_event: Event,
        heartbeat: Any = None
    ):
        self.worker_id = worker_id
        self.config = config
        self.input_queue = input_queue
        self.output_queue = output_queue
        self.db_queue = db_queue
        self.stop_event = stop_event
        self.heartbeat = heartbeat
        
        # Per-feed state
        self.traffic_monitors: Dict[str, TrafficMonitor] = {}
        self.safety_monitors: Dict[str, SafetyMonitor] = {}

    def run(self):
        """Main worker loop."""
        pid = os.getpid()
        logger.info(f"[AnalyticsWorker-{self.worker_id}] Starting process {pid}")
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        
        # Internal performance counters
        last_log_time = time.time()
        processed_since_log = 0
        total_unpickle_time = 0.0
        total_process_time = 0.0
        
        try:
            while not self.stop_event.is_set():
                try:
                    # 1. Batch-drain input queue for higher throughput
                    raw_items = []
                    try:
                        # Reduced batch size from 50 to 10 for better heartbeat granularity
                        for _ in range(10):
                            raw_items.append(self.input_queue.get_nowait())
                    except queue.Empty:
                        pass
                    
                    if not raw_items:
                        # Update heartbeat even when idle
                        if self.heartbeat:
                            self.heartbeat.value = time.time()
                        time.sleep(0.01)
                        continue

                    start_wait = time.time()
                    
                    for item in raw_items:
                        # Update heartbeat for every item processed
                        if self.heartbeat:
                            self.heartbeat.value = time.time()

                        unpickle_time = time.time() - start_wait
                        total_unpickle_time += unpickle_time
                    
                        loop_start = time.time()
                        feed_id, frame_index, timestamp, vehicles, lanes, lines, worker_metrics, extra = item
                    
                        # --- Track Quality Gating ---
                        min_q = self.config.get("analytics", {}).get("min_track_quality", 0.4)
                    
                        # Mark vehicles as reliable or not, but keep all for visual continuity
                        reliable_vehicles = []
                        for v in vehicles:
                            is_reliable = v.get("quality_score", 1.0) >= min_q
                            v["is_reliable"] = is_reliable
                            if is_reliable:
                                reliable_vehicles.append(v)
                    
                        # 2. Lazy-init monitors
                        if feed_id not in self.traffic_monitors:
                            self.traffic_monitors[feed_id] = TrafficMonitor(self.config)
                            self.safety_monitors[feed_id] = SafetyMonitor(self.config)
                            logger.info(f"[AnalyticsWorker] Initialized monitors for feed {feed_id}")

                        t_monitor = self.traffic_monitors[feed_id]
                        s_monitor = self.safety_monitors[feed_id]

                        # 3. Run Safety Analytics (Stopped Vehicle, Wrong Way)
                        safety_alerts = s_monitor.update(feed_id, reliable_vehicles, timestamp)
                    
                        # 3b. Handle Calibration Drift
                        calib = extra.get("calibration") if extra else None
                        if calib:
                            is_drifted = calib.get("is_drifted", False)
                            if is_drifted:
                                drift_score = calib.get("drift_score", 0.0)
                                safety_alerts.append({
                                    "type": "safety_alert",
                                    "subtype": "calibration_drift",
                                    "severity": "high",
                                    "feed_id": feed_id,
                                    "description": f"Camera calibration drift detected (Score: {drift_score:.4f}). Speeds may be inaccurate.",
                                    "timestamp": timestamp,
                                    "meta": {"drift_score": drift_score}
                                })

                        # 4. Update Traffic Metrics
                        # TrafficMonitor.update_vehicles expects a DICT of reliable vehicles for internal tracking
                        reliable_map = {v["vehicle_id"]: v for v in reliable_vehicles}
                        t_monitor.update_vehicles(reliable_map)
                        feed_metrics = t_monitor.get_metrics()
                    
                        # Merge worker performance metrics and extra metadata
                        feed_metrics.update(worker_metrics)
                        if calib:
                            feed_metrics["calibration"] = calib
                        
                        feed_metrics["frame_index"] = frame_index
                        feed_metrics["timestamp"] = timestamp
                        feed_metrics["reliable_track_count"] = len(reliable_vehicles)
                        feed_metrics["noise_track_count"] = len(vehicles) - len(reliable_vehicles)
                    
                        # 5. Handle Alerts (Send to DB Queue)
                        for alert in safety_alerts:
                            try:
                                self.db_queue.put(
                                    {"type": "safety_alert", "feed_id": feed_id, "data": alert},
                                    block=False
                                )
                            except queue.Full:
                                logger.warning("[AnalyticsWorker] DB queue full. Alert dropped.")

                        # 6. Push final processed payload to output queue for UI broadcast
                        if frame_index % self.config.get("analytics", {}).get("broadcast_interval", 5) == 0:
                            try:
                                # Backpressure check
                                if self.output_queue.qsize() > 500:
                                    logger.warning(f"[AnalyticsWorker] Output queue backing up ({self.output_queue.qsize()} items).")
                            
                                self.output_queue.put((feed_id, feed_metrics, vehicles, lanes, lines), block=False)
                            except queue.Full:
                                logger.error("[AnalyticsWorker] Output queue saturated. Dropping analytics update.")

                        proc_time = time.time() - loop_start
                        total_process_time += proc_time
                        processed_since_log += 1
                    
                        if proc_time > 1.0:
                            logger.warning(f"[AnalyticsWorker] Slow iteration for {feed_id}: {proc_time:.3f}s (Vehicles: {len(vehicles)})")
                    
                        # Reset wait timer for next item in batch
                        start_wait = time.time()
                    
                    # Periodic Diagnostic Summary
                    now = time.time()
                    if now - last_log_time > 60.0:
                        avg_proc = total_process_time / processed_since_log if processed_since_log > 0 else 0
                        avg_unp = total_unpickle_time / processed_since_log if processed_since_log > 0 else 0
                        logger.info(
                            f"[AnalyticsWorker] Diagnostics: Processed={processed_since_log}, "
                            f"AvgProc={avg_proc*1000:.1f}ms, AvgUnpickle={avg_unp*1000:.1f}ms"
                        )
                        last_log_time = now
                        processed_since_log = 0
                        total_process_time = 0.0
                        total_unpickle_time = 0.0

                except Exception as e:
                    import traceback
                    logger.error(f"[AnalyticsWorker] Loop error: {e}\n{traceback.format_exc()}")
                    time.sleep(0.1)

        except Exception as e:
            import traceback
            logger.error(f"[AnalyticsWorker] Fatal error: {e}\n{traceback.format_exc()}")
        finally:
            logger.info(f"Analytics process {pid} terminated.")

def analytics_worker_process(worker_id, config, input_q, output_q, db_q, stop_event, heartbeat=None):
    """Entry point for multiprocessing."""
    # Initialize global config for this process
    from ..config import set_config
    set_config(config)

    # Initialize logging for the child process
    import logging.config
    try:
        logging.config.dictConfig(config["logging"])
    except Exception as e:
        pass  # Logging config failed, will use default
    
    worker = AnalyticsWorker(worker_id, config, input_q, output_q, db_q, stop_event, heartbeat)
    worker.run()
