import math
import numpy as np
import logging
from typing import Dict, List, Tuple, Optional, Any, Callable
import uuid
import time
from filterpy.kalman import KalmanFilter
from scipy.optimize import linear_sum_assignment

logger = logging.getLogger("app.ml.tracking")

# No global counter - using UUIDs for guaranteed uniqueness across workers/restarts

class TrackingManager:
    def __init__(self, config: dict, fps: int, feed_id: str = "default"):
        self.config = config
        self.fps = fps
        self.feed_id = feed_id
        self.vehicle_data: Dict[str, Dict] = {}
        
        # Configuration parameters
        tracking_cfg = config.get("tracking", {})
        self.proximity_threshold = tracking_cfg.get("proximity_threshold", 150)
        self.track_timeout = tracking_cfg.get("track_timeout", 30)
        self.track_timeout_unit = tracking_cfg.get("track_timeout_unit", "frames")  # "frames" or "seconds"
        self.dynamic_matching_threshold = tracking_cfg.get("dynamic_matching_threshold", 0.7)
        self.low_conf_association_threshold = tracking_cfg.get("low_conf_association_threshold", 0.5)
        self.appearance_weight = tracking_cfg.get("appearance_weight", 0.5)
        self.velocity_gate_boost = tracking_cfg.get("velocity_gate_boost", 1.5)
        self.base_gate_multiplier = tracking_cfg.get("base_gate_multiplier", 1.0)
        self.use_appearance_in_tracking = tracking_cfg.get("use_appearance_in_tracking", True)
        self.stationary_cleanup_timeout = tracking_cfg.get("stationary_cleanup_timeout", 300)
        self.stationary_cleanup_enabled = tracking_cfg.get("stationary_cleanup_enabled", False)
        
        # Use class-level counter for global uniqueness
        # No instance counter needed - using UUIDs
        # Callback for track expiration cleanup
        self.on_track_expired: Optional[Callable] = None

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
        # More realistic noise modeling
        kf.P *= 10.0  # Initial covariance
        # Measurement noise: position has higher uncertainty
        kf.R = np.eye(4) * 0.1
        # Process noise: velocity noise higher than position noise
        kf.Q = np.diag([0.01, 0.01, 0.01, 0.01,  # position noise
                       0.1, 0.1, 0.1, 0.1])     # velocity noise
        
        return kf

    def update(self, detections: List[Tuple], current_time: float, frame_shape: Tuple[int, int], track_timeout: Optional[int] = None, proximity_threshold: Optional[int] = None) -> Dict[str, Dict]:
        """Runs the tracking association pipeline (ByteTrack based)."""
        if track_timeout is not None:
            self.track_timeout = track_timeout
        if proximity_threshold is not None:
            self.proximity_threshold = proximity_threshold

        new_or_updated_tracks = {}
        h, w = frame_shape[:2]
        
        # Use provided track_timeout or fall back to default
        effective_track_timeout = track_timeout if track_timeout is not None else self.track_timeout
        
        # 1. Separate detections
        high_conf_dets = []
        low_conf_dets = []
        # FIX: Read threshold from the correct config section
        CONF_THRESH = self.config.get("vehicle_detection", {}).get("confidence_threshold", 0.3)
        LOW_CONF_THRESH = self.config.get("vehicle_detection", {}).get("low_confidence_threshold", 0.1)
        
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
                kf.F[2, 6] = dt
                kf.F[3, 7] = dt
                kf.predict()
                
                tx, ty, tw, th = kf.x[0][0], kf.x[1][0], kf.x[2][0], kf.x[3][0]
                
                # Fix #16: Clamp predicted state to frame bounds
                tx = max(0, min(tx, w))
                ty = max(0, min(ty, h))
                tw = max(1, min(tw, w))  # Width must be positive
                th = max(1, min(th, h))  # Height must be positive
                
                # Update Kalman state with clamped values
                kf.x[0][0] = tx
                kf.x[1][0] = ty
                kf.x[2][0] = tw
                kf.x[3][0] = th
                
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
        # FIX: Ensure we match IDs correctly (tid is the key in self.vehicle_data)
        # Convert matched_tracks_1 to set of strings for proper comparison
        matched_ids_str = set(str(mid) for mid in matched_tracks_1)
        unmatched_tracks_1 = [t for tid, t in self.vehicle_data.items() if str(tid) not in matched_ids_str]

        # 4. Second Association: Low Confidence (IoU Only)
        matched_tracks_2 = set()
        if low_conf_dets and unmatched_tracks_1:
            cost_matrix_2 = self._calculate_cost_matrix(low_conf_dets, unmatched_tracks_1, use_reid=False)
            if cost_matrix_2.size > 0:
                row_ind, col_ind = linear_sum_assignment(cost_matrix_2)
                for r, c in zip(row_ind, col_ind):
                    if cost_matrix_2[r, c] < self.low_conf_association_threshold:
                        track = unmatched_tracks_1[c]
                        self._update_track(track, low_conf_dets[r], current_time)
                        matched_tracks_2.add(track["vehicle_id"])
                        new_or_updated_tracks[track["vehicle_id"]] = track

        # 5. Finalize matches and handle lost
        final_matched = matched_tracks_1.union(matched_tracks_2)
        for tid, track in self.vehicle_data.items():
            if tid not in final_matched:
                # Convert timeout to seconds based on configured unit
                if self.track_timeout_unit == "seconds":
                    timeout_sec = effective_track_timeout
                else:
                    # Default: treat as frames, convert to seconds
                    timeout_sec = effective_track_timeout / self.fps
                if (current_time - track["last_seen"]) < timeout_sec:
                    track["status"] = "predicting"
                    if "predicted_bbox" in track:
                        track["bbox"] = track["predicted_bbox"]
                    # Clip to frame - but don't silently drop, mark as out_of_frame
                    px1, py1, px2, py2 = track["bbox"]
                    if px2 < 0 or px1 > w or py2 < 0 or py1 > h:
                        track["status"] = "out_of_frame"
                        new_or_updated_tracks[tid] = track
                    else:
                        new_or_updated_tracks[tid] = track
                else:
                    # Track expired - trigger cleanup callback
                    if self.on_track_expired:
                        self.on_track_expired(track)
                    # Don't include in new_or_updated_tracks (track is dropped)


# 6. Initialize New Tracks
        for det in unmatched_dets_1:
            new_track = self._create_new_track(det, current_time)
            new_or_updated_tracks[new_track["vehicle_id"]] = new_track
            
        self.vehicle_data.clear()
        self.vehicle_data.update(new_or_updated_tracks)
        return self.vehicle_data.copy()

    def _calculate_cost_matrix(self, detections, tracks, use_reid=True):
        if not detections or not tracks:
            return np.empty((len(detections), len(tracks)))

        num_dets = len(detections)
        num_tracks = len(tracks)
        
        # Extract bboxes for vectorization: [N, 4]
        det_boxes = np.array([d[0] for d in detections], dtype=np.float32)
        track_boxes = np.array([t["predicted_bbox"] for t in tracks], dtype=np.float32)
        
        # Calculate Centroids
        det_centroids = np.stack([(det_boxes[:, 0] + det_boxes[:, 2]) / 2, 
                                  (det_boxes[:, 1] + det_boxes[:, 3]) / 2], axis=1)
        track_centroids = np.stack([(track_boxes[:, 0] + track_boxes[:, 2]) / 2, 
                                   (track_boxes[:, 1] + track_boxes[:, 3]) / 2], axis=1)
        
        # 1. Compute GIoU (Vectorized)
        # det_boxes: [N, 1, 4], track_boxes: [1, M, 4]
        b1 = det_boxes[:, np.newaxis, :]
        b2 = track_boxes[np.newaxis, :, :]
        
        xA = np.maximum(b1[..., 0], b2[..., 0])
        yA = np.maximum(b1[..., 1], b2[..., 1])
        xB = np.minimum(b1[..., 2], b2[..., 2])
        yB = np.minimum(b1[..., 3], b2[..., 3])
        
        inter = np.maximum(0, xB - xA) * np.maximum(0, yB - yA)
        areaA = (b1[..., 2] - b1[..., 0]) * (b1[..., 3] - b1[..., 1])
        areaB = (b2[..., 2] - b2[..., 0]) * (b2[..., 3] - b2[..., 1])
        union = areaA + areaB - inter
        iou = inter / (union + 1e-6)
        
        ex = np.minimum(b1[..., 0], b2[..., 0])
        ey = np.minimum(b1[..., 1], b2[..., 1])
        ex2 = np.maximum(b1[..., 2], b2[..., 2])
        ey2 = np.maximum(b1[..., 3], b2[..., 3])
        e_area = (ex2 - ex) * (ey2 - ey)
        giou = iou - (e_area - union) / (e_area + 1e-6)
        
        # 2. Compute Euclidean Distance (Vectorized)
        # det_centroids: [N, 2], track_centroids: [M, 2]
        dist = np.linalg.norm(det_centroids[:, np.newaxis, :] - track_centroids[np.newaxis, :, :], axis=2)
        
        # 3. Gating & Final Costs
        costs = np.full((num_dets, num_tracks), 10000.0)
        
        gate_giou = -0.5
        gate_dist = self.proximity_threshold * self.base_gate_multiplier
        
        # Apply velocity boost to the distance gate per track
        # track_velocities: [M]
        track_vels = np.array([math.sqrt(t.get("vx", 0)**2 + t.get("vy", 0)**2) for t in tracks])
        boosts = np.where(track_vels > 0.1, self.velocity_gate_boost, 1.0)
        effective_gate_dist = gate_dist * boosts # [M]
        
        # Mask for gated pairs: (giou > gate) AND (dist < gate_dist)
        mask = (giou > gate_giou) & (dist < effective_gate_dist)
        
        # Motion cost
        motion_cost = 1.0 - giou
        
        # ReID cost (Fallback to loops only for ReID as embeddings vary in presence)
        if use_reid:
            reid_costs = np.zeros((num_dets, num_tracks))
            for d in range(num_dets):
                det_emb = detections[d][3]
                if det_emb is None:
                    reid_costs[d, :] = 1.0
                    continue
                norm_det_emb = det_emb / (np.linalg.norm(det_emb) + 1e-6)
                for t in range(num_tracks):
                    track_emb = tracks[t].get("embedding")
                    if track_emb is not None:
                        norm_track_emb = track_emb / (np.linalg.norm(track_emb) + 1e-6)
                        reid_costs[d, t] = (1.0 - np.dot(norm_det_emb, norm_track_emb)) * self.appearance_weight
                    else:
                        reid_costs[d, t] = 1.0
            total_cost = motion_cost + reid_costs
        else:
            total_cost = motion_cost
            
        costs[mask] = total_cost[mask]
        return costs

    def _update_track(self, track, det, current_time):
        bbox, cls, conf, emb = det
        # FIX: Update prev_status for occlusion-recovery triggers
        track["prev_status"] = track.get("status", "unknown")
        track["bbox"] = bbox
        track["last_seen"] = current_time
        track["status"] = "active"
        track["confidence"] = conf
        track["class_id"] = cls
        # FIX: Update centroid
        track["centroid"] = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
        
        # Update last_moved_time if vehicle has shifted significantly
        if "ground_coordinates" in track:
            curr_pos = track["ground_coordinates"]
            prev_pos = track.get("prev_ground_pos")
            if prev_pos:
                dist = math.sqrt((curr_pos[0]-prev_pos[0])**2 + (curr_pos[1]-prev_pos[1])**2)
                if dist > 0.1: # Moved more than 10cm
                    track["last_moved_time"] = current_time
            else:
                track["last_moved_time"] = current_time
        
        # Update Kalman
        kf = track.get("kalman_filter")
        if kf:
            cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            kf.update(np.array([[cx], [cy], [w], [h]]))
            
            # Extract pixel velocity from Kalman state [x, y, w, h, vx, vy, vw, vh]
            track["vx"] = float(kf.x[4][0])
            track["vy"] = float(kf.x[5][0])

    def _create_new_track(self, det, current_time):
        bbox, cls, conf, emb = det
# global_id_counter removed - using UUID-based IDs
        # FIX: Use feed_id prefix for globally unique IDs
        # Generate globally unique ID: feed_id + timestamp + short UUID
        ts = int(current_time * 1000)  # milliseconds
        short_uuid = str(uuid.uuid4())[:8]
        vid = f"{self.feed_id}_{ts}_{short_uuid}"
        return {
            "vehicle_id": vid,
            "bbox": bbox,
            "centroid": ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2),
            "class_id": cls,
            "confidence": conf,
            "last_seen": current_time,
            "status": "active",
            "kalman_filter": self._init_kalman((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2, bbox[2]-bbox[0], bbox[3]-bbox[1]),
            "embedding": emb,
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
        # Proper GIoU: penalizes both non-overlap and spatial separation
        giou = iou - (e_area - union) / e_area
        return giouB[3])
        e_area = (ex2 - ex) * (ey2 - ey)
        # Proper GIoU: penalizes both non-overlap and spatial separation
        giou = iou - (e_area - union) / e_area
        return giou