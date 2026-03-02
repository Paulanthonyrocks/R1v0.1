import time
import math
import logging
from typing import Dict, List, Optional, Tuple, Any
from app.services.lane_calibrator import LaneCalibrator

logger = logging.getLogger(__name__)

class SafetyMonitor:
    """
    Monitors vehicle states for unsafe behaviors:
    1. Stopped Vehicles (in active lanes)
    2. Wrong-Way Drivers
    """
    
    def __init__(self, config: Dict):
        self.config = config
        self.safety_config = config.get("safety_analytics", {})
        
        # Thresholds
        self.stopped_speed_threshold = self.safety_config.get("stopped_speed_kmh", 5.0)
        self.stopped_duration_threshold = self.safety_config.get("stopped_duration_sec", 10.0)
        self.wrong_way_cosine_threshold = self.safety_config.get("wrong_way_cosine_threshold", -0.8)
        self.wrong_way_duration_threshold = self.safety_config.get("wrong_way_duration_sec", 1.5)
        
        # Lane Calibrator (Operational Autonomy)
        self.lane_calibrator = LaneCalibrator(
            confidence_threshold=self.safety_config.get("calibration_confidence", 0.8)
        )
        
        # State Tracking
        # { vehicle_id: { "stopped_since": timestamp, "last_speed": float } }
        self.vehicle_states: Dict[str, Dict] = {}
        
        # Alert Cooldowns to prevent spam
        # { vehicle_id_alert_type: last_alert_timestamp }
        self.alert_cooldowns: Dict[str, float] = {}
        self.alert_cooldown_sec = 30.0

    def update(self, feed_id: str, vehicles: List[Dict], timestamp: float) -> List[Dict]:
        """
        Process a frame of vehicles and return a list of generated alerts (if any).
        """
        alerts = []
        current_ids = set()
        
        # Get Static Flow Config if available
        feed_config = self.config.get("feeds", {}).get(feed_id, {})
        static_flow_vector = feed_config.get("flow_vector", None)
        
        for v in vehicles:
            vid = v["vehicle_id"]
            current_ids.add(vid)
            
            # Feed samples to lane calibrator
            vx, vy = v.get("vx"), v.get("vy")
            lane_id = v.get("lane", -1)
            if vx is not None and vy is not None:
                self.lane_calibrator.add_sample(feed_id, lane_id, vx, vy)
            
            # 1. Stopped Vehicle Detection
            alert = self._check_stopped(feed_id, v, timestamp)
            if alert:
                alerts.append(alert)
            
            # 2. Wrong-Way Detection
            # Prioritize learned vector, fall back to static config
            flow_vector, confidence = self.lane_calibrator.get_flow_vector(feed_id, lane_id)
            
            # Use learned vector if confidence is high, else use static if available
            effective_vector = None
            source = "none"
            
            if flow_vector and confidence >= self.lane_calibrator.confidence_threshold:
                effective_vector = flow_vector
                source = "calibrated"
            elif static_flow_vector:
                effective_vector = static_flow_vector
                source = "static"
                
            if effective_vector:
                alert = self._check_wrong_way(feed_id, v, effective_vector, timestamp)
                if alert:
                    v["is_wrong_way"] = True
                    alert["meta"]["vector_source"] = source
                    alert["meta"]["calibration_confidence"] = confidence
                    alerts.append(alert)
                else:
                    v["is_wrong_way"] = False
                    
            # Set stopped flag if applicable
            v["is_stopped"] = False
            if vid in self.vehicle_states and "stopped_since" in self.vehicle_states[vid]:
                duration = timestamp - self.vehicle_states[vid]["stopped_since"]
                if duration > self.stopped_duration_threshold:
                    v["is_stopped"] = True
                
        # Cleanup stale states
        for missing_id in list(self.vehicle_states.keys()):
            if missing_id not in current_ids:
                del self.vehicle_states[missing_id]
        
        # Periodic cleanup of alert cooldowns (every 1000 frames roughly)
        if timestamp % 100 < 1.0: # simplistic throttle
            now = time.time()
            for key in list(self.alert_cooldowns.keys()):
                if now - self.alert_cooldowns[key] > 3600: # 1 hour
                    del self.alert_cooldowns[key]
                
        return alerts

    def _check_stopped(self, feed_id: str, vehicle: Dict, timestamp: float) -> Optional[Dict]:
        vid = vehicle["vehicle_id"]
        # Calculate speed in km/h roughly from pixels/sec if not available, 
        # but CoreModule should provide 'speed' (scalar) or 'velocity' (vector).
        # Prioritize scalar speed if calibrated, else magnitude of velocity vector.
        
        speed = vehicle.get("speed", 0.0)
        if "vx" in vehicle and "vy" in vehicle:
            # Velocity vector available for additional precision if needed
            pass
            
        state = self.vehicle_states.get(vid, {})
        
        if speed < self.stopped_speed_threshold:
            if "stopped_since" not in state:
                state["stopped_since"] = timestamp
            
            duration = timestamp - state["stopped_since"]
            if duration > self.stopped_duration_threshold:
                # Trigger Alert
                if self._should_alert(vid, "stopped", timestamp):
                    return {
                        "type": "safety_alert",
                        "subtype": "stopped_vehicle",
                        "severity": "high",
                        "feed_id": feed_id,
                        "vehicle_id": vid,
                        "description": f"Vehicle {vid} stopped for {duration:.1f}s",
                        "timestamp": timestamp,
                        "meta": {"duration": duration, "speed": speed}
                    }
        else:
            # Reset if moving
            state.pop("stopped_since", None)
            
        self.vehicle_states[vid] = state
        return None

    def _check_wrong_way(self, feed_id: str, vehicle: Dict, lane_vector: List[float], timestamp: float) -> Optional[Dict]:
        vid = vehicle["vehicle_id"]
        vx = vehicle.get("vx")
        vy = vehicle.get("vy")
        if vx is None or vy is None:
            return None
        
        # Normalize vehicle vector
        mag = math.sqrt(vx*vx + vy*vy)
        if mag < 2.0: # Higher threshold for wrong-way to avoid noise at low speeds
            return None 
        
        norm_vx, norm_vy = vx/mag, vy/mag
        
        # Dot product
        dot = norm_vx * lane_vector[0] + norm_vy * lane_vector[1]
        
        state = self.vehicle_states.get(vid, {})
        
        if dot < self.wrong_way_cosine_threshold:
            if "wrong_way_since" not in state:
                state["wrong_way_since"] = timestamp
            
            duration = timestamp - state["wrong_way_since"]
            if duration > self.wrong_way_duration_threshold:
                if self._should_alert(vid, "wrong_way", timestamp):
                    return {
                        "type": "safety_alert",
                        "subtype": "wrong_way",
                        "severity": "critical",
                        "feed_id": feed_id,
                        "vehicle_id": vid,
                        "description": f"Vehicle {vid} sustained wrong-way driving ({duration:.1f}s)",
                        "timestamp": timestamp,
                        "meta": {"alignment": dot, "duration": duration, "velocity": [vx, vy]}
                    }
        else:
            state.pop("wrong_way_since", None)
            
        self.vehicle_states[vid] = state
        return None

    def _should_alert(self, vehicle_id: str, alert_type: str, timestamp: float) -> bool:
        key = f"{vehicle_id}_{alert_type}"
        last_alert = self.alert_cooldowns.get(key, 0)
        if timestamp - last_alert > self.alert_cooldown_sec:
            self.alert_cooldowns[key] = timestamp
            return True
        return False
