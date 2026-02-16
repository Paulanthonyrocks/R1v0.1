import math
import numpy as np
import logging
from collections import deque, Counter
from typing import Dict, List, Tuple, Optional, Any
from filterpy.kalman import KalmanFilter
from scipy.optimize import linear_sum_assignment

logger = logging.getLogger("app.ml.tracking")

class TrackingManager:
    def __init__(self, config: dict, fps: int):
        self.config = config
        self.fps = fps
        self.vehicle_data: Dict[str, Dict] = {}
        
        # Configuration parameters
        tracking_cfg = config.get("tracking", {})
        self.proximity_threshold = tracking_cfg.get("proximity_threshold", 150)
        self.track_timeout = tracking_cfg.get("track_timeout", 30)
        self.dynamic_matching_threshold = tracking_cfg.get("dynamic_matching_threshold", 0.7)
        self.appearance_weight = tracking_cfg.get("appearance_weight", 0.5)
        self.velocity_gate_boost = tracking_cfg.get("velocity_gate_boost", 1.5)
        self.base_gate_multiplier = tracking_cfg.get("base_gate_multiplier", 1.0)
        self.use_appearance_in_tracking = tracking_cfg.get("use_appearance_in_tracking", True)
        self.stationary_cleanup_timeout = tracking_cfg.get("stationary_cleanup_timeout", 300)
        
        self.global_id_counter = 0

    def _init_kalman(self, cx, cy, w, h):
        """Initializes a Kalman Filter for a new track."""
        kf = KalmanFilter(dim_x=8, dim_z=4)
        dt = 1.0 / self.fps
        
        # State: [x, y, w, h, vx, vy, vw, vh]
        kf.x = np.array([[cx], [cy], [w], [h], [0], [0], [0], [0]])
        
        # Transition matrix
        kf.F = np.eye(8)
        kf.F[0, 4] = dt
        kf.F[1, 5] = dt
        kf.F[2, 6] = dt
        kf.F[3, 7] = dt
        
        # Measurement matrix
        kf.H = np.zeros((4, 8))
        kf.H[0, 0] = 1
        kf.H[1, 1] = 1
        kf.H[2, 2] = 1
        kf.H[3, 3] = 1
        
        # Covariance matrices
        kf.P *= 10.0
        kf.R *= 1.0
        kf.Q *= 0.1
        
        return kf

    def update(self, detections: List[Tuple], current_time: float, frame_shape: Tuple[int, int]) -> Dict[str, Dict]:
        """Runs the tracking association pipeline (ByteTrack based)."""
        new_or_updated_tracks = {}
        h, w = frame_shape[:2]
        
        # 1. Separate detections
        high_conf_dets = []
        low_conf_dets = []
        CONF_THRESH = self.config.get("confidence_threshold", 0.3)
        LOW_CONF_THRESH = 0.1
        
        for det in detections:
            bbox, cls, conf, emb = det
            if conf >= CONF_THRESH:
                high_conf_dets.append(det)
            elif conf >= LOW_CONF_THRESH:
                low_conf_dets.append(det)

        # 2. Kalman Prediction
        for tid, track in self.vehicle_data.items():
            kf = track.get("kalman_filter")
            if kf:
                dt = current_time - track.get("last_prediction_time", track["last_seen"])
                if dt <= 0.001: dt = 1.0 / self.fps
                
                kf.F[0, 4] = dt
                kf.F[1, 5] = dt
                kf.predict()
                
                tx, ty, tw, th = kf.x[0][0], kf.x[1][0], kf.x[2][0], kf.x[3][0]
                track["predicted_bbox"] = (tx - tw/2, ty - th/2, tx + tw/2, ty + th/2)
                track["last_prediction_time"] = current_time

        # 3. First Association: High Confidence (IoU + ReID)
        matched_tracks_1 = set()
        matched_dets_1 = set()
        
        if high_conf_dets and self.vehicle_data:
            track_pool = list(self.vehicle_data.values())
            cost_matrix_1 = self._calculate_cost_matrix(high_conf_dets, track_pool, use_reid=True)
            
            if cost_matrix_1.size > 0:
                row_ind, col_ind = linear_sum_assignment(cost_matrix_1)
                for r, c in zip(row_ind, col_ind):
                    if cost_matrix_1[r, c] < self.dynamic_matching_threshold:
                        track = track_pool[c]
                        self._update_track(track, high_conf_dets[r], current_time)
                        matched_tracks_1.add(track["vehicle_id"])
                        matched_dets_1.add(r)
                        new_or_updated_tracks[track["vehicle_id"]] = track

        unmatched_dets_1 = [d for i, d in enumerate(high_conf_dets) if i not in matched_dets_1]
        unmatched_tracks_1 = [t for tid, t in self.vehicle_data.items() if tid not in matched_tracks_1]

        # 4. Second Association: Low Confidence (IoU Only)
        matched_tracks_2 = set()
        if low_conf_dets and unmatched_tracks_1:
            cost_matrix_2 = self._calculate_cost_matrix(low_conf_dets, unmatched_tracks_1, use_reid=False)
            if cost_matrix_2.size > 0:
                row_ind, col_ind = linear_sum_assignment(cost_matrix_2)
                for r, c in zip(row_ind, col_ind):
                    if cost_matrix_2[r, c] < 0.5: # Fixed low conf thresh
                        track = unmatched_tracks_1[c]
                        self._update_track(track, low_conf_dets[r], current_time)
                        matched_tracks_2.add(track["vehicle_id"])
                        new_or_updated_tracks[track["vehicle_id"]] = track

        # 5. Finalize matches and handle lost
        final_matched = matched_tracks_1.union(matched_tracks_2)
        for tid, track in self.vehicle_data.items():
            if tid not in final_matched:
                if (current_time - track["last_seen"]) < self.track_timeout:
                    track["status"] = "predicting"
                    if "predicted_bbox" in track:
                        track["bbox"] = track["predicted_bbox"]
                        # Clip to frame
                        px1, py1, px2, py2 = track["bbox"]
                        if px2 < 0 or px1 > w or py2 < 0 or py1 > h: continue
                        new_or_updated_tracks[tid] = track

        # 6. Initialize New Tracks
        for det in unmatched_dets_1:
            new_track = self._create_new_track(det, current_time)
            new_or_updated_tracks[new_track["vehicle_id"]] = new_track
            
        self.vehicle_data = new_or_updated_tracks
        return self.vehicle_data

    def _calculate_cost_matrix(self, detections, tracks, use_reid=True):
        num_dets = len(detections)
        num_tracks = len(tracks)
        costs = np.full((num_dets, num_tracks), 10000.0)
        
        for d, (det_bbox, det_cls, det_conf, det_emb) in enumerate(detections):
            det_cx = (det_bbox[0] + det_bbox[2]) / 2
            det_cy = (det_bbox[1] + det_bbox[3]) / 2
            
            for t, track in enumerate(tracks):
                if "predicted_bbox" in track:
                    giou = self._bbox_giou(det_bbox, track["predicted_bbox"])
                    tr_cx = (track["predicted_bbox"][0] + track["predicted_bbox"][2]) / 2
                    tr_cy = (track["predicted_bbox"][1] + track["predicted_bbox"][3]) / 2
                    dist = math.sqrt((det_cx - tr_cx)**2 + (det_cy - tr_cy)**2)
                    
                    if giou > -0.5 or dist < 150: # Gate
                        motion_cost = 1.0 - giou
                        reid_cost = 0.0
                        if use_reid and det_emb is not None and "embedding" in track:
                            reid_cost = (1.0 - np.dot(det_emb, track["embedding"])) * self.appearance_weight
                        
                        costs[d, t] = motion_cost + reid_cost
        return costs

    def _update_track(self, track, det, current_time):
        bbox, cls, conf, emb = det
        track["bbox"] = bbox
        
        # --- Velocity Bootstrapping ---
        # If track is young (<= 5 frames), boost velocity estimate using simple displacement
        # This helps the KF converge faster than waiting for Q/R to settle
        track_age = len(track.get("class_history", []))
        if 1 <= track_age <= 5:
            dt = current_time - track.get("last_seen", current_time)
            if dt > 0.001:
                prev_cx, prev_cy = track["centroid"]
                curr_cx, curr_cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
                vx = (curr_cx - prev_cx) / dt
                vy = (curr_cy - prev_cy) / dt
                
                kf = track.get("kalman_filter")
                if kf:
                    # State: [x, y, w, h, vx, vy, vw, vh]
                    kf.x[4][0] = vx
                    kf.x[5][0] = vy
                    
        track["last_seen"] = current_time
        track["status"] = "active"
        track["confidence"] = conf
        
        # --- Class Stabilization ---
        # Instead of instant switching, use voting window
        if "class_history" not in track:
            track["class_history"] = deque([track["class_id"]], maxlen=10)
        track["class_history"].append(cls)
        
        # Simple majority vote
        counts = Counter(track["class_history"])
        most_common = counts.most_common(1)
        if most_common:
            # Only switch if we have a strong lead or enough samples
            winner, count = most_common[0]
            if count >= 3: # minimal stability
                track["class_id"] = winner

        # Update Kalman
        kf = track.get("kalman_filter")
        if kf:
            cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            kf.update(np.array([[cx], [cy], [w], [h]]))

    def _create_new_track(self, det, current_time):
        bbox, cls, conf, emb = det
        self.global_id_counter += 1
        return {
            "vehicle_id": f"TRK_{self.global_id_counter}",
            "bbox": bbox,
            "centroid": ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2),
            "class_id": cls,
            "confidence": conf,
            "last_seen": current_time,
            "status": "active",
            "kalman_filter": self._init_kalman((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2, bbox[2]-bbox[0], bbox[3]-bbox[1]),
            "embedding": emb,
            "class_history": deque([cls], maxlen=10),
            "speed_history": deque(maxlen=5),
            "smoothed_speed": 0.0,
            "speed": 0.0,
            "last_speed_update_time": current_time,
        }

    def _bbox_giou(self, boxA, boxB):
        xA, yA, xB, yB = max(boxA[0], boxB[0]), max(boxA[1], boxB[1]), min(boxA[2], boxB[2]), min(boxA[3], boxB[3])
        inter = max(0, xB - xA) * max(0, yB - yA)
        areaA = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
        areaB = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
        union = areaA + areaB - inter
        iou = inter / (union + 1e-6)
        ex, ey, ex2, ey2 = min(boxA[0], boxB[0]), min(boxA[1], boxB[1]), max(boxA[2], boxB[2]), max(boxA[3], boxB[3])
        e_area = (ex2 - ex) * (ey2 - ey)
        return iou - (e_area - union) / (e_area + 1e-6)
