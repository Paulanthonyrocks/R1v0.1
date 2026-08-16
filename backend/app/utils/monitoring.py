import logging
import time
import psutil  # Needed for check_system_resources
from typing import Dict, Any, Tuple, Optional, List
from collections import deque, defaultdict, OrderedDict
import numpy as np

logger = logging.getLogger(__name__)


class FrameTimer:
    """
    Advanced timer for tracking frame processing stages and calculating FPS.
    Combines stage-based timing with frequency-based FPS counting.
    """
    def __init__(self, window_size: int = 100):
        self._start_times = {}
        self._metrics = {}
        self.timings = defaultdict(lambda: deque(maxlen=window_size))
        self.last_tick = time.time()

    def start(self, stage: str):
        """Start timing a specific processing stage."""
        self._start_times[stage] = time.time()

    def stop(self, stage: str):
        """Stop timing a specific processing stage and log the duration."""
        if stage in self._start_times:
            duration = time.time() - self._start_times[stage]
            self._metrics[stage] = duration
            self.log_time(stage, duration)
            return duration
        return 0.0

    def tick(self, name: str = "loop_total"):
        """
        Record a 'tick' for a given metric (e.g., end of a loop).
        Calculates duration since the last tick.
        """
        now = time.time()
        duration = now - self.last_tick
        self.last_tick = now
        self.log_time(name, duration)

    def log_time(self, name: str, duration: float):
        """Manually log a duration for a specific metric."""
        self.timings[name].append(duration)

    def get_avg(self, name: str) -> float:
        """Get the average duration for a specific metric over the sliding window."""
        if not self.timings[name]:
            # Fallback to _metrics if available
            return self._metrics.get(name, 0.0)
        return sum(self.timings[name]) / len(self.timings[name])

    def get_fps(self, name: str = "loop_total") -> float:
        """Calculate FPS based on the average duration of a specific metric."""
        avg_time = self.get_avg(name)
        return 1.0 / avg_time if avg_time > 0.0 else 0.0

    def get_metrics(self) -> Dict[str, float]:
        """Get current instantaneous metrics."""
        return self._metrics.copy()


def check_system_resources(cpu_interval: float = 0.1) -> Tuple[float, float]:
    """Checks current CPU and Virtual Memory usage percentage."""
    try:
        cpu_percent = psutil.cpu_percent(interval=cpu_interval)
        memory_info = psutil.virtual_memory()
        memory_percent = memory_info.percent
        return cpu_percent, memory_percent
    except Exception as e:
        logger.error(f"Failed to get system resource usage: {e}", exc_info=True)
        return 0.0, 0.0


class TrafficMonitor:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.tracked_vehicles: Dict[str, Dict[str, Any]] = {}
        self.lane_counts: Dict[int, int] = {}
        # Bounded dedup set for cumulative counting (audit finding #3).
        # Previously an unbounded `set()` that grew for the entire feed lifetime
        # -> memory leak on long-running feeds. We keep a monotonic counter for
        # the cumulative total (so the broadcast value never decreases) and use
        # an insertion-ordered dict as a FIFO-bounded dedup set: once it exceeds
        # `max_seen_ids` we evict the oldest entries. A vehicle whose id is
        # evicted and later re-seen may be counted twice, but that is a rare,
        # bounded inaccuracy -- far better than unbounded memory growth.
        self.cumulative_vehicle_count: int = 0
        self.max_seen_ids: int = int(config.get("traffic_monitor", {}).get("max_seen_ids", 200_000))
        # OrderedDict: insertion order is preserved and popitem(last=False)
        # evicts the oldest id -> FIFO-bounded dedup set (robust on all Pythons,
        # unlike dict.popitem(last=...) which some builds reject).
        self.seen_vehicle_ids: "OrderedDict[Any, None]" = OrderedDict()
        # Local track_ids that have already been counted this feed. A vehicle is
        # first seen under its local track_id (before ReID assigns a global id);
        # once it gets a global_vehicle_id that is the SAME physical vehicle, so
        # we must not count it again under the new id (audit fix: intra-feed
        # double-count when track_id -> global_vehicle_id transition occurs).
        self.seen_local_ids: "OrderedDict[Any, None]" = OrderedDict()
        self.session_metrics = {
            "speed_sum": 0.0,
            "speed_samples": 0,
            "congestion_sum": 0.0,
            "congestion_samples": 0,
            "congestion_score_sum": 0.0,
            "congestion_score_samples": 0
        }
        # Recent-window rolling samples for LIVE KPIs. The session_* metrics
        # above are since-boot cumulative means -- they asymptote toward the
        # run's average and never reflect current conditions (observed: global
        # congestion pinned at 72.9-73.5 for a whole 23-min run while the
        # per-frame value swung 60-98). The dashboard headline KPI needs a
        # short-window mean instead: keep a bounded deque of (ts, speed,
        # congestion_score) samples and average over the configured window.
        self.recent_window_seconds: float = float(config.get("metrics_recent_window_seconds", 60))
        self._recent_samples: "deque[tuple]" = deque(maxlen=256)
        self.lane_areas: Dict[int, float] = {}
        self.anomalies: List[Dict[str, Any]] = []
        self.speed_limit_kmh: float = config.get("speed_limit", 60.0)

        incident_cfg = config.get("incident_detection", {})
        self.density_threshold: int = incident_cfg.get("density_threshold", 10)
        self.congestion_speed_threshold: float = incident_cfg.get("congestion_speed_threshold", 20.0)
        self.stopped_threshold_kmh: float = config.get("stopped_speed_threshold_kmh", 5.0)

        # Hard-braking gate. Operator-tunable via behavior_analysis.accel_threshold_mps2
        # (audit fix #3): previously _detect_anomalies hard-coded -8.0 m/s², so the
        # config value was silently ignored. A vehicle decelerating harder than this
        # (more negative accel) is flagged as hard_braking. Default -8.0 preserves the
        # prior behavior when the key is absent.
        behavior_cfg = config.get("behavior_analysis", {})
        self.hard_braking_accel_threshold_mps2: float = -abs(
            behavior_cfg.get("accel_threshold_mps2", 8.0)
        )

    def update_vehicles(self, vehicles: Dict[str, Dict[str, Any]]):
        self.tracked_vehicles = vehicles
        self.lane_counts.clear()
        for track_id, data in vehicles.items():
            # Use global ID if available for unique counting, otherwise fallback to local track_id
            unique_id = data.get("global_vehicle_id") or track_id
            gid = data.get("global_vehicle_id")

            if gid:
                if track_id in self.seen_local_ids:
                    # Already counted this physical vehicle under its local
                    # track_id before ReID resolved the global id -> do NOT
                    # count again; just record the global id for stability.
                    self.seen_vehicle_ids[gid] = None
                elif gid not in self.seen_vehicle_ids:
                    self.cumulative_vehicle_count += 1
                    self.seen_vehicle_ids[gid] = None
                self.seen_local_ids[track_id] = None
            else:
                if track_id not in self.seen_local_ids:
                    if track_id not in self.seen_vehicle_ids:
                        self.cumulative_vehicle_count += 1
                    self.seen_vehicle_ids[track_id] = None
                    self.seen_local_ids[track_id] = None

            # Bound memory: evict oldest ids once over cap (FIFO via insertion order)
            while len(self.seen_vehicle_ids) > self.max_seen_ids:
                self.seen_vehicle_ids.popitem(last=False)
            while len(self.seen_local_ids) > self.max_seen_ids:
                self.seen_local_ids.popitem(last=False)
            
            lane = data.get("lane", -1)
            if lane != -1:
                try:
                    lane = int(lane)
                except (ValueError, TypeError):
                    pass
                self.lane_counts[lane] = self.lane_counts.get(lane, 0) + 1
        
        if len(self.anomalies) > 100:
            self.anomalies = self.anomalies[-100:]
        self._detect_anomalies(vehicles)

    def _detect_anomalies(self, vehicles: Dict[str, Dict[str, Any]]):
        now = time.time()
        for v_id, data in vehicles.items():
            accel = data.get("acceleration", 0.0)
            if accel < self.hard_braking_accel_threshold_mps2:
                self.anomalies.append({
                    "type": "hard_braking",
                    "vehicle_id": v_id,
                    "timestamp": now,
                    "severity": "Warning",
                    "details": f"Sudden deceleration detected: {accel:.1f} m/s²",
                    "location": data.get("centroid")
                })
            # Wrong-way detection is handled authoritatively by SafetyMonitor
            # (velocity vs. learned/static lane flow vector). The previous
            # heuristic here read a 'direction' field against a hardcoded
            # compass value and was both incoherent and redundant, so it has
            # been removed (audit C2).

    def get_metrics(self) -> Dict[str, Any]:
        current_vehicle_count = len(self.tracked_vehicles)
        stopped_count = 0
        speeding_count = 0
        speeds_list_kmh: list[float] = []
        vehicle_type_map = {
            2: "car",
            3: "motorcycle",
            5: "bus",
            7: "truck",
            -1: "unknown",
        }
        vehicle_type_counts: Dict[str, int] = {name: 0 for name in vehicle_type_map.values()}
        if "unknown" not in vehicle_type_counts:
            vehicle_type_counts["unknown"] = 0

        for data in self.tracked_vehicles.values():
            speed_kmh = float(data.get("speed", 0.0))
            speeds_list_kmh.append(speed_kmh)
            if speed_kmh < self.stopped_threshold_kmh:
                stopped_count += 1
            if speed_kmh > self.speed_limit_kmh:
                speeding_count += 1
            class_id = data.get("class_id", -1)
            type_name = vehicle_type_map.get(class_id, "unknown")
            vehicle_type_counts[type_name] = vehicle_type_counts.get(type_name, 0) + 1

        avg_speed_kmh = float(np.median(speeds_list_kmh)) if speeds_list_kmh else 0.0
        congestion_lvl_percent = float((stopped_count / current_vehicle_count) * 100.0) if current_vehicle_count > 0 else 0.0
        
        if current_vehicle_count > 0:
            self.session_metrics["speed_sum"] += avg_speed_kmh
            self.session_metrics["speed_samples"] += 1
            self.session_metrics["congestion_sum"] += congestion_lvl_percent
            self.session_metrics["congestion_samples"] += 1

        session_avg_speed = self.session_metrics["speed_sum"] / self.session_metrics["speed_samples"] if self.session_metrics["speed_samples"] > 0 else 0.0
        session_avg_congestion = self.session_metrics["congestion_sum"] / self.session_metrics["congestion_samples"] if self.session_metrics["congestion_samples"] > 0 else 0.0
        is_congested = avg_speed_kmh < self.congestion_speed_threshold and current_vehicle_count > self.density_threshold
        high_density_lanes = [lane for lane, count in self.lane_counts.items() if count > self.density_threshold]

        congestion_score = 0.0
        if current_vehicle_count > 0:
            speed_factor = 1 - (avg_speed_kmh / self.speed_limit_kmh) if self.speed_limit_kmh > 0 else 0
            speed_factor = max(0, min(1, speed_factor))
            density_factor = current_vehicle_count / 100.0
            density_factor = max(0, min(1, density_factor))
            congestion_score = (speed_factor * 0.7 + density_factor * 0.3) * 100
            congestion_score = round(congestion_score, 1)
            
            self.session_metrics["congestion_score_sum"] += congestion_score
            self.session_metrics["congestion_score_samples"] += 1

            # Recent-window rolling samples for LIVE KPIs (see __init__ note).
            # Append one sample per observed frame and prune to the window;
            # averaging over these gives a short-horizon mean that actually
            # tracks current conditions instead of the since-boot session mean.
            self._recent_samples.append((time.time(), avg_speed_kmh, congestion_score))
            _now = time.time()
            while self._recent_samples and (self._recent_samples[0][0] < _now - self.recent_window_seconds):
                self._recent_samples.popleft()

        recent_speeds = [s[1] for s in self._recent_samples if s[1] is not None]
        recent_congestions = [s[2] for s in self._recent_samples if s[2] is not None]
        recent_avg_speed = float(np.mean(recent_speeds)) if recent_speeds else 0.0
        recent_avg_congestion = float(np.mean(recent_congestions)) if recent_congestions else 0.0

        session_avg_congestion_score = self.session_metrics["congestion_score_sum"] / self.session_metrics["congestion_score_samples"] if self.session_metrics["congestion_score_samples"] > 0 else 0.0

        lane_occupancy: Dict[int, float] = {}
        lane_queues: Dict[int, float] = {}
        
        # Ensure all configured lanes are represented, even if empty
        lane_cfg = self.config.get("lane_detection", {})
        num_lanes = lane_cfg.get("num_lanes", 4)
        for i in range(1, num_lanes + 1):
            lane_occupancy[i] = 0.0
            lane_queues[i] = 0.0

        for lane_id, count in self.lane_counts.items():
            lane_occupancy[lane_id] = min(100.0, (count * 15.0))
            lane_vehicles = [v for v in self.tracked_vehicles.values() if v.get("lane") == lane_id]
            stopped_in_lane = [v for v in lane_vehicles if v.get("speed", 0) < self.stopped_threshold_kmh]
            if len(stopped_in_lane) >= 3:
                lane_queues[lane_id] = len(stopped_in_lane) * 6.0
            else:
                lane_queues[lane_id] = 0.0

        return {
            "total_vehicles": current_vehicle_count,
            "total_vehicles_cumulative": self.cumulative_vehicle_count,
            "session_average_speed_kmh": round(session_avg_speed, 1),
            "session_congestion_level_percent": round(session_avg_congestion, 1),
            "session_average_congestion_score": round(session_avg_congestion_score, 1),
            "recent_average_speed_kmh": round(recent_avg_speed, 1),
            "recent_average_congestion_score": round(recent_avg_congestion, 1),
            "stopped_vehicles": stopped_count,
            "speeding_vehicles": speeding_count,
            "average_speed_kmh": round(avg_speed_kmh, 1),
            "congestion_level_percent": round(congestion_lvl_percent, 1),
            "is_congested": is_congested,
            "congestion_score": congestion_score,
            "vehicles_per_lane": self.lane_counts.copy(),
            "high_density_lanes": high_density_lanes,
            "vehicle_type_counts": vehicle_type_counts,
            "lane_occupancy": lane_occupancy,
            "queue_lengths": lane_queues,
            "anomalies": self.anomalies[-5:]
        }
