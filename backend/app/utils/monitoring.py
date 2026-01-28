import logging
import time
import psutil  # Needed for check_system_resources
from typing import Dict, Any, Tuple, Optional
from collections import deque, defaultdict
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
    # Class attribute: Mapping of vehicle class IDs to their names.
    vehicle_type_map: Dict[int, str] = {
        2: "car",
        3: "motorcycle",
        5: "bus",
        7: "truck",
        -1: "unknown",
    }

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.tracked_vehicles: Dict[int, Dict[str, Any]] = {}
        self.lane_counts: Dict[int, int] = {}
        self.seen_vehicle_ids = set()
        self.session_metrics = {
            "speed_sum": 0.0,
            "speed_samples": 0,
            "congestion_sum": 0.0,
            "congestion_samples": 0
        }
        self.lane_areas: Dict[int, float] = {} 
        self.anomalies: List[Dict[str, Any]] = []
        self.speed_limit_kmh: float = config.get("speed_limit", 60.0)

        incident_cfg = config.get("incident_detection", {})
        self.density_threshold: int = incident_cfg.get("density_threshold", 10)
        self.congestion_speed_threshold: float = incident_cfg.get("congestion_speed_threshold", 20.0)
        self.stopped_threshold_kmh: float = config.get("stopped_speed_threshold_kmh", 5.0)

    def update_vehicles(self, vehicles: Dict[int, Dict[str, Any]]):
        self.tracked_vehicles = vehicles
        self.lane_counts.clear()
        for track_id, data in vehicles.items():
            self.seen_vehicle_ids.add(track_id)
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

    def _detect_anomalies(self, vehicles: Dict[int, Dict[str, Any]]):
        now = time.time()
        for v_id, data in vehicles.items():
            accel = data.get("acceleration", 0.0)
            if accel < -8.0:
                self.anomalies.append({
                    "type": "hard_braking",
                    "vehicle_id": v_id,
                    "timestamp": now,
                    "severity": "Warning",
                    "details": f"Sudden deceleration detected: {accel:.1f} m/s²",
                    "location": data.get("centroid")
                })
            direction = data.get("direction")
            if direction == "North" and data.get("vy", 0) > 2.0:
                 self.anomalies.append({
                    "type": "wrong_way",
                    "vehicle_id": v_id,
                    "timestamp": now,
                    "severity": "Critical",
                    "details": "Vehicle traveling against lane flow",
                    "location": data.get("centroid")
                })

    def get_metrics(self) -> Dict[str, Any]:
        current_vehicle_count = len(self.tracked_vehicles)
        stopped_count = 0
        speeding_count = 0
        speeds_list_kmh: list[float] = []
        vehicle_type_counts: Dict[str, int] = {name: 0 for name in self.vehicle_type_map.values()}
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
            type_name = self.vehicle_type_map.get(class_id, "unknown")
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

        lane_occupancy: Dict[int, float] = {}
        lane_queues: Dict[int, float] = {}
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
            "total_vehicles_cumulative": len(self.seen_vehicle_ids),
            "session_average_speed_kmh": round(session_avg_speed, 1),
            "session_congestion_level_percent": round(session_avg_congestion, 1),
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