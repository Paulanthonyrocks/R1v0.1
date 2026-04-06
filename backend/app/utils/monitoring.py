import logging
import time
import psutil
from typing import Dict, Any, Tuple, Optional, List
from collections import deque, defaultdict
import numpy as np
import math

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
        self.tracked_vehicles: Dict[str, Dict[str, Any]] = {}
        self.lane_counts: Dict[int, int] = {}
        self.lane_speeds: Dict[int, deque] = defaultdict(lambda: deque(maxlen=100))
        self.seen_vehicle_ids = set()
        self.last_seen_cleanup = time.time()
        self.session_metrics = {
            "speed_sum": 0.0,
            "speed_samples": 0,
            "congestion_sum": 0.0,
            "congestion_samples": 0,
            "congestion_score_sum": 0.0,
            "congestion_score_samples": 0
        }
        self.lane_areas: Dict[int, float] = {}
        self.anomalies: List[Dict[str, Any]] = []
        self.speed_limit_kmh: float = config.get("speed_limit", 60.0)

        incident_cfg = config.get("incident_detection", {})
        self.density_threshold: int = incident_cfg.get("density_threshold", 10)
        self.congestion_speed_threshold: float = incident_cfg.get("congestion_speed_threshold", 20.0)
        self.stopped_threshold_kmh: float = config.get("stopped_speed_threshold_kmh", 5.0)
        
        # Health metrics
        self.health_history = deque(maxlen=100)
        self.track_continuity_samples = deque(maxlen=100)
        self.last_update_time = time.time()
        self.smoothed_congestion_score = 0.0
        self.congestion_alpha = config.get("behavior_analysis", {}).get("ewma_alpha", 0.1)

    def update_vehicles(self, vehicles: Dict[str, Dict[str, Any]]):
        self.tracked_vehicles = vehicles
        self.lane_counts.clear()
        
        active_count = 0
        total_conf = 0.0
        
        for track_id, data in vehicles.items():
            if "centroid" not in data or data["centroid"] is None:
                if "bbox" in data and data["bbox"]:
                    b = data["bbox"]
                    data["centroid"] = [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]
            
            if not data.get("centroid"):
                continue

            unique_id = data.get("global_vehicle_id") or track_id
            self.seen_vehicle_ids.add(unique_id)
            
            if len(self.seen_vehicle_ids) > 10000:
                self.seen_vehicle_ids.clear()
                self.last_seen_cleanup = time.time()
                logger.warning(f"TrafficMonitor: seen_vehicle_ids set cleared due to size limit.")
            
            status = data.get("status", "active")
            if status == "active":
                active_count += 1
                total_conf += data.get("confidence", 0.0)
            
            lane = data.get("lane", -1)
            if lane != -1:
                try:
                    lane = int(lane)
                except (ValueError, TypeError):
                    pass
                self.lane_counts[lane] = self.lane_counts.get(lane, 0) + 1
                
                speed = data.get("speed")
                if speed is not None:
                    self.lane_speeds[lane].append(speed)
        
        total_tracked = len(vehicles)
        continuity = (active_count / total_tracked) if total_tracked > 0 else 1.0
        self.track_continuity_samples.append(continuity)
        
        avg_conf = (total_conf / active_count) if active_count > 0 else 0.8 
        
        now = time.time()
        gap = now - self.last_update_time
        self.last_update_time = now
        
        gap_score = max(0, min(1, 1.0 - (gap / 2.0)))
        
        h_score = (avg_conf * 0.4 + continuity * 0.4 + gap_score * 0.2) * 100
        self.health_history.append(h_score)

        if len(self.anomalies) > 100:
            self.anomalies = self.anomalies[-100:]
        self._detect_anomalies(vehicles)

    def _detect_anomalies(self, vehicles: Dict[str, Dict[str, Any]]):
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
            speed_penalty = 1.0 - (avg_speed_kmh / self.speed_limit_kmh) if self.speed_limit_kmh > 0 else 1.0
            speed_penalty = max(0, min(1, speed_penalty))
            density = current_vehicle_count / 40.0
            raw_congestion_score = min(100.0, (density * speed_penalty * 80.0) + (density * 20.0))
            
            if self.smoothed_congestion_score == 0.0:
                self.smoothed_congestion_score = raw_congestion_score
            else:
                self.smoothed_congestion_score = (self.congestion_alpha * raw_congestion_score) + \
                                                ((1 - self.congestion_alpha) * self.smoothed_congestion_score)
            
            congestion_score = round(self.smoothed_congestion_score, 1)
            
            self.session_metrics["congestion_score_sum"] += congestion_score
            self.session_metrics["congestion_score_samples"] += 1
        else:
            self.smoothed_congestion_score *= (1 - self.congestion_alpha)
            if self.smoothed_congestion_score < 0.1:
                self.smoothed_congestion_score = 0.0
            congestion_score = round(self.smoothed_congestion_score, 1)

        lane_cfg = self.config.get("lane_detection", {})
        num_lanes = lane_cfg.get("num_lanes", 4)
        current_lane_counts = {i: 0 for i in range(1, num_lanes + 1)}
        lane_occupancy = {i: 0.0 for i in range(1, num_lanes + 1)}
        lane_queues = {i: 0.0 for i in range(1, num_lanes + 1)}
        
        for v in self.tracked_vehicles.values():
            lid = v.get("lane", -1)
            if 1 <= lid <= num_lanes:
                current_lane_counts[lid] += 1

        lane_gaps, lane_headways = self._calculate_gap_and_headway(num_lanes)
        
        lane_speed_profiles = {}
        for i in range(1, num_lanes + 1):
            count = current_lane_counts[i]
            lane_occupancy[i] = min(100.0, (count * 15.0))
            lane_vehicles = [v for v in self.tracked_vehicles.values() if v.get("lane") == i]
            stopped_in_lane = [v for v in lane_vehicles if v.get("speed", 0) < self.stopped_threshold_kmh]
            if len(stopped_in_lane) >= 3:
                lane_queues[i] = len(stopped_in_lane) * 6.0
            
            speeds = list(self.lane_speeds.get(i, []))
            if len(speeds) >= 5:
                lane_speed_profiles[i] = {
                    "avg": round(float(np.mean(speeds)), 1),
                    "p85": round(float(np.percentile(speeds, 85)), 1),
                    "std": round(float(np.std(speeds)), 1)
                }
            else:
                lane_speed_profiles[i] = {"avg": 0.0, "p85": 0.0, "std": 0.0}

        avg_health = float(np.mean(self.health_history)) if self.health_history else 100.0
        health_status = "Healthy"
        if avg_health < 50: health_status = "Critical"
        elif avg_health < 80: health_status = "Degraded"

        return {
            "total_vehicles": current_vehicle_count,
            "total_vehicles_cumulative": len(self.seen_vehicle_ids),
            "session_average_speed_kmh": round(session_avg_speed, 1),
            "session_congestion_level_percent": round(session_avg_congestion, 1),
            "session_average_congestion_score": round(session_avg_congestion_score, 1),
            "stopped_vehicles": stopped_count,
            "speeding_vehicles": speeding_count,
            "average_speed_kmh": round(avg_speed_kmh, 1),
            "congestion_level_percent": round(congestion_lvl_percent, 1),
            "is_congested": is_congested,
            "congestion_score": congestion_score,
            "vehicles_per_lane": current_lane_counts,
            "high_density_lanes": high_density_lanes,
            "vehicle_type_counts": vehicle_type_counts,
            "lane_occupancy": lane_occupancy,
            "queue_lengths": lane_queues,
            "lane_speed_profiles": lane_speed_profiles,
            "avg_headway_sec": round(float(np.mean(list(lane_headways.values()))) if lane_headways else 0.0, 2),
            "min_gap_meters": round(float(np.min(list(lane_gaps.values()))) if lane_gaps else 0.0, 1),
            "anomalies": self.anomalies[-5:],
            "health_score": round(avg_health, 1),
            "health_status": health_status
        }

    def _calculate_gap_and_headway(self, num_lanes: int) -> Tuple[Dict[int, float], Dict[int, float]]:
        lane_gaps = {}
        lane_headways = {}
        now = time.time()
        for lane_id in range(1, num_lanes + 1):
            lane_vehicles = []
            for v in self.tracked_vehicles.values():
                if v.get("lane") == lane_id:
                    if "centroid" not in v and "bbox" in v:
                        b = v["bbox"]
                        v["centroid"] = [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]
                    if "centroid" in v: lane_vehicles.append(v)
            if len(lane_vehicles) < 2: continue
            lane_vehicles.sort(key=lambda v: v["centroid"][1], reverse=True) 
            gaps = []
            headways = []
            for i in range(len(lane_vehicles) - 1):
                v_front = lane_vehicles[i+1] 
                v_back = lane_vehicles[i]    
                p1 = v_front.get("ground_coordinates")
                p2 = v_back.get("ground_coordinates")
                if p1 and p2:
                    dist = math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2)
                    speed = v_back.get("speed", 0.0) / 3.6 
                    if dist > 0:
                        gaps.append(dist)
                        if speed > 5.0: 
                            headway = dist / speed
                            headways.append(headway)
                            if headway < 1.0:
                                self.anomalies.append({
                                    "type": "tailgating",
                                    "vehicle_id": v_back.get("vehicle_id", "unknown"),
                                    "timestamp": now,
                                    "severity": "Warning",
                                    "details": f"Tailgating detected: Headway {headway:.2f}s",
                                    "location": v_back.get("centroid")
                                })
            if gaps: lane_gaps[lane_id] = float(np.mean(gaps))
            if headways: lane_headways[lane_id] = float(np.mean(headways))
        return lane_gaps, lane_headways
