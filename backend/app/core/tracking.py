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
        if fps <= 0:
            logger.warning(f"Invalid fps {fps} provided for feed {feed_id}. Defaulting to 30 to prevent division by zero.")
            self.fps = 30
        else:
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
        self.giou_threshold = tracking_cfg.get("giou_threshold", -0.5)
        self.max_gate_dist = tracking_cfg.get("max_gate_dist", 500)
        self.embedding_dim = tracking_cfg.get("embedding_dim", 128)
        # A track must be seen this many consecutive frames before it becomes
        # "active" and reaches the wire. Below it the track is "tentative" and
        # is dropped on the first miss. vehicle_detection.probation_threshold
        # (config.yaml) and tracking.probation_threshold (tests) are both
        # honored; default 3 = one detection is never a vehicle.
        vd_cfg = config.get("vehicle_detection", {})
        self.probation_threshold = tracking_cfg.get(
            "probation_threshold", vd_cfg.get("probation_threshold", 3)
        )
        
        # Kalman noise parameters
        self.kalman_r = tracking_cfg.get("kalman_r", 0.1)
        self.kalman_q_pos = tracking_cfg.get("kalman_q_pos", 0.01)
        self.kalman_q_vel = tracking_cfg.get("kalman_q_vel", 0.1)

        # Log the effective association/tracking tunables once at tracker init.
        # These are the knobs that gate moving-car association (proximity *
        # velocity_gate_boost) and moving-car prediction (kalman_q_vel); a
        # deploy copy that predates a tuning change silently keeps the OLD
        # behavior and this log makes the divergence visible in main.log.
        logger.info(
            f"[{self.feed_id}] Tracking tuning: proximity={self.proximity_threshold} "
            f"velocity_gate_boost={self.velocity_gate_boost} max_gate_dist={self.max_gate_dist} "
            f"giou_threshold={self.giou_threshold} track_timeout={self.track_timeout}{self.track_timeout_unit} "
            f"kalman_q_vel={self.kalman_q_vel} dynamic_matching_threshold={self.dynamic_matching_threshold} "
            f"probation_threshold={self.probation_threshold}"
        )
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
        kf.P *= 10.0  # Initial covariance
        kf.R = np.eye(4) * self.kalman_r
        kf.Q = np.diag([self.kalman_q_pos] * 4 + [self.kalman_q_vel] * 4)
        
        return kf

    def update(self, detections: List[Tuple], current_time: float, frame_shape: Tuple[int, int], track_timeout: Optional[int] = None, proximity_threshold: Optional[int] = None) -> Dict[str, Dict]:
        """
        Runs the tracking association pipeline (ByteTrack based).
        
        Note: track_timeout overrides are applied instantly to the current frame's expiration logic 
        and may cause tracks to expire earlier or later than the original configuration intended.
        """
        # Use provided overrides or fall back to instance defaults for this call only
        effective_track_timeout = track_timeout if track_timeout is not None else self.track_timeout
        effective_proximity_threshold = proximity_threshold if proximity_threshold is not None else self.proximity_threshold

        new_or_updated_tracks = {}
        h, w = frame_shape[:2]
        frame_diagonal = math.sqrt(w**2 + h**2)
        
        # 1. Separate detections
        high_conf_dets = []
        low_conf_dets = []
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
                
                # Prevent inverted boxes by ensuring positive dimensions and valid corner ordering
                tw = max(1.0, tw)
                th = max(1.0, th)
                x1 = max(0.0, tx - tw/2)
                y1 = max(0.0, ty - th/2)
                x2 = min(float(w), tx + tw/2)
                y2 = min(float(h), ty + th/2)
                
                if x1 >= x2:
                    x2 = min(x1 + 1.0, float(w))
                if y1 >= y2:
                    y2 = min(y1 + 1.0, float(h))
                
                # Clamp predicted bbox to frame bounds for the result, but do NOT alter kf.x
                track["predicted_bbox"] = (x1, y1, x2, y2)
                track["last_prediction_time"] = current_time
                
                # Update velocity from prediction for distance gate boost
                vx_pred = np.nan_to_num(kf.x[4][0], nan=0.0)
                vy_pred = np.nan_to_num(kf.x[5][0], nan=0.0)
                track["vx"] = float(np.clip(vx_pred, -5000, 5000))
                track["vy"] = float(np.clip(vy_pred, -5000, 5000))

        # 3. First Association: High Confidence (IoU + ReID)
        matched_tracks_1 = set()
        matched_dets_1 = set()
        
        if high_conf_dets and self.vehicle_data:
            track_pool = list(self.vehicle_data.values())
            # Use the configuration flag to determine if appearance (ReID) should be used
            use_reid = self.use_appearance_in_tracking
            cost_matrix_1 = self._calculate_cost_matrix(high_conf_dets, track_pool, use_reid=use_reid, proximity_threshold=effective_proximity_threshold, frame_diagonal=frame_diagonal)
            
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
            cost_matrix_2 = self._calculate_cost_matrix(low_conf_dets, unmatched_tracks_1, use_reid=False, proximity_threshold=effective_proximity_threshold, frame_diagonal=frame_diagonal)
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
                # Tentative tracks die on the first miss. They never graduated
                # probation (3 consistent hits), so a one-frame detection
                # speck is gone immediately instead of lingering as a
                # duplicate box for track_timeout frames — that class of
                # lingering is what rendered as multiple boxes trailing a
                # single vehicle along its path.
                if track.get("status") == "tentative":
                    continue

                # Maintain a frame counter for truly frame-based timeouts
                track["frames_since_seen"] = track.get("frames_since_seen", 0) + 1
                
                is_within_timeout = False
                if self.track_timeout_unit == "seconds":
                    timeout_sec = effective_track_timeout
                    if (current_time - track["last_seen"]) < timeout_sec:
                        is_within_timeout = True
                else:
                    # Truly frame-based: no dependence on self.fps, no drift
                    if track["frames_since_seen"] < effective_track_timeout:
                        is_within_timeout = True
                
                if is_within_timeout:
                    # Only inflate covariance on the first transition to 'predicting'
                    if track.get("status") != "predicting":
                        track["status"] = "predicting"
                        kf = track.get("kalman_filter")
                        if kf:
                            # Set to a fixed high value instead of multiplying to prevent exponential growth
                            # over multiple lost/regained cycles.
                            kf.P[4, 4] = 1000.0
                            kf.P[5, 5] = 1000.0
                    
                    if "predicted_bbox" in track:
                        track["bbox"] = track["predicted_bbox"]
                    
                    new_or_updated_tracks[tid] = track
                else:
                    if self.on_track_expired:
                        self.on_track_expired(track)


        # 6. Initialize New Tracks
        for det in unmatched_dets_1:
            new_track = self._create_new_track(det, current_time)
            new_or_updated_tracks[new_track["vehicle_id"]] = new_track
            
        self.vehicle_data.clear()
        self.vehicle_data.update(new_or_updated_tracks)
        return self.vehicle_data

    @staticmethod
    def _compute_pairwise_giou(b1: np.ndarray, b2: np.ndarray) -> np.ndarray:
        """
        Calculates GIoU for a set of pairs of bounding boxes.
        b1, b2: (K, 4) arrays of [x1, y1, x2, y2]
        Returns: (K,) array of GIoU values.
        """
        xA = np.maximum(b1[:, 0], b2[:, 0])
        yA = np.maximum(b1[:, 1], b2[:, 1])
        xB = np.minimum(b1[:, 2], b2[:, 2])
        yB = np.minimum(b1[:, 3], b2[:, 3])

        inter = np.maximum(0, xB - xA) * np.maximum(0, yB - yA)
        w1 = np.maximum(1.0, b1[:, 2] - b1[:, 0])
        h1 = np.maximum(1.0, b1[:, 3] - b1[:, 1])
        w2 = np.maximum(1.0, b2[:, 2] - b2[:, 0])
        h2 = np.maximum(1.0, b2[:, 3] - b2[:, 1])
        areaA = w1 * h1
        areaB = w2 * h2
        union = areaA + areaB - inter
        iou = inter / (union + 1e-6)

        ex = np.minimum(b1[:, 0], b2[:, 0])
        ey = np.minimum(b1[:, 1], b2[:, 1])
        ex2 = np.maximum(b1[:, 2], b2[:, 2])
        ey2 = np.maximum(b1[:, 3], b2[:, 3])
        ew = np.maximum(1.0, ex2 - ex)
        eh = np.maximum(1.0, ey2 - ey)
        e_area = ew * eh
        giou = iou - (e_area - union) / (e_area + 1e-6)
        return giou

    def _calculate_cost_matrix(self, detections, tracks, use_reid=True, proximity_threshold=None, frame_diagonal=None):
        if not detections or not tracks:
            return np.empty((len(detections), len(tracks)))

        num_dets = len(detections)
        num_tracks = len(tracks)
        
        det_boxes = np.array([d[0] for d in detections], dtype=np.float32)
        track_boxes = np.array([t["predicted_bbox"] for t in tracks], dtype=np.float32)
        
        det_centroids = np.stack([(det_boxes[:, 0] + det_boxes[:, 2]) / 2, 
                                  (det_boxes[:, 1] + det_boxes[:, 3]) / 2], axis=1)
        track_centroids = np.stack([(track_boxes[:, 0] + track_boxes[:, 2]) / 2, 
                                   (track_boxes[:, 1] + track_boxes[:, 3]) / 2], axis=1)
        
        # Pre-filter with distance gate to save CPU on expensive GIoU/ReID
        dist = np.linalg.norm(det_centroids[:, np.newaxis, :] - track_centroids[np.newaxis, :, :], axis=2)
        
        costs = np.full((num_dets, num_tracks), 10000.0)
        
        thresh = proximity_threshold if proximity_threshold is not None else self.proximity_threshold
        gate_dist = thresh * self.base_gate_multiplier
        
        track_vels = np.array([math.sqrt(np.clip(t.get("vx", 0)**2, 0, 1e12) + np.clip(t.get("vy", 0)**2, 0, 1e12)) for t in tracks])
        boosts = np.where(track_vels > 0.1, self.velocity_gate_boost, 1.0)
        
        # Adaptive max gate distance: use the configured value, but cap it at 50% of frame diagonal if provided
        max_dist = self.max_gate_dist
        if frame_diagonal:
            max_dist = min(max_dist, 0.5 * frame_diagonal)
            
        effective_gate_dist = np.minimum(gate_dist * boosts, max_dist)
        
        dist_mask = dist < effective_gate_dist
        if not np.any(dist_mask):
            return costs

        # Only compute expensive metrics for candidates passing the distance gate
        det_idx, track_idx = np.where(dist_mask)
        
        # 1. Compute GIoU only for gated pairs
        pairwise_giou = self._compute_pairwise_giou(det_boxes[det_idx], track_boxes[track_idx])
        motion_costs = 1.0 - pairwise_giou
        
        # 2. Compute ReID costs only for gated pairs
        reid_costs = np.zeros_like(pairwise_giou)
        if use_reid:
            det_embs_all = [d[3] for d in detections]
            track_embs_all = [t.get("embedding") for t in tracks]
            
            if any(e is not None for e in det_embs_all) and any(e is not None for e in track_embs_all):
                # Dynamically determine embedding dimension from the first available embedding to avoid mismatches
                actual_dim = next((e.shape[0] for e in det_embs_all if e is not None), self.embedding_dim)
                
                det_embs = np.array([det_embs_all[i] if det_embs_all[i] is not None else np.zeros(actual_dim) for i in det_idx])
                track_embs = np.array([track_embs_all[j] if track_embs_all[j] is not None else np.zeros(actual_dim) for j in track_idx])
                
                # Vectorized cosine distance for gated pairs
                det_norms = np.linalg.norm(det_embs, axis=1, keepdims=True) + 1e-6
                track_norms = np.linalg.norm(track_embs, axis=1, keepdims=True) + 1e-6
                
                # Compute dot product for corresponding pairs
                dot_products = np.sum((det_embs / det_norms) * (track_embs / track_norms), axis=1)
                reid_costs = (1.0 - dot_products) * self.appearance_weight
                
                # Mask out ReID costs where either embedding was missing
                missing_mask = np.array([det_embs_all[i] is None or track_embs_all[j] is None for i, j in zip(det_idx, track_idx)])
                reid_costs[missing_mask] = 0.0
            
        total_costs = motion_costs + reid_costs
        
        # Final mask: distance gate (already applied via indices) AND GIoU gate
        final_mask = pairwise_giou > self.giou_threshold
        
        # Apply results back to the cost matrix
        # We only update the indices that passed the distance gate AND the GIoU gate
        valid_det_idx = det_idx[final_mask]
        valid_track_idx = track_idx[final_mask]
        costs[valid_det_idx, valid_track_idx] = total_costs[final_mask]
        
        return costs

    def _update_track(self, track, det, current_time):
        bbox, cls, conf, emb = det
        track["prev_status"] = track.get("status", "unknown")
        track["bbox"] = bbox
        track["last_seen"] = current_time
        # Probation: a track converges to "active" only after N consecutive
        # hits. Until then it stays "tentative" and the consumer pipeline
        # (core_module vis_tracks, inference_worker _LIVE_TRACK_STATUSES)
        # excludes it from the wire, DB, and ReID collection.
        track["hits"] = track.get("hits", 0) + 1
        track["status"] = "active" if track["hits"] >= self.probation_threshold else "tentative"
        track["confidence"] = conf
        track["class_id"] = cls
        track["frames_since_seen"] = 0
        track["age"] = track.get("age", 0) + 1
        if emb is not None:
            track["embedding"] = emb
        track["centroid"] = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
        
        kf = track.get("kalman_filter")
        if kf:
            cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            try:
                kf.update(np.array([[cx], [cy], [w], [h]]))
                # Sanitize velocities: replace NaNs with 0 then clip to safe range
                vx_val = np.nan_to_num(kf.x[4][0], nan=0.0)
                vy_val = np.nan_to_num(kf.x[5][0], nan=0.0)
                track["vx"] = float(np.clip(vx_val, -5000, 5000))
                track["vy"] = float(np.clip(vy_val, -5000, 5000))
            except Exception as e:
                logger.warning(f"Kalman update failed for track {track['vehicle_id']}: {e}")

    def _create_new_track(self, det, current_time):
        bbox, cls, conf, emb = det
        # Simplified ID: feed_id + 16-char hex suffix to prevent collisions
        vid = f"{self.feed_id}_{uuid.uuid4().hex[:16]}"
        return {
            "vehicle_id": vid,
            "bbox": bbox,
            "predicted_bbox": bbox,
            "centroid": ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2),
            "class_id": cls,
            "confidence": conf,
            "last_seen": current_time,
            "last_prediction_time": current_time,
            "status": "tentative",
            "hits": 1,
            "prev_status": "unknown",
            "age": 1,
            "kalman_filter": self._init_kalman((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2, bbox[2]-bbox[0], bbox[3]-bbox[1]),
            "embedding": emb,
        }
