import math
import uuid
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
        vd_cfg = config.get("vehicle_detection", {})
        self.proximity_threshold = tracking_cfg.get("proximity_threshold") or vd_cfg.get("proximity_threshold") or 250
        self.track_timeout = tracking_cfg.get("track_timeout") or vd_cfg.get("track_timeout") or 30
        self.dynamic_matching_threshold = tracking_cfg.get("dynamic_matching_threshold", 0.7)
        self.appearance_weight = tracking_cfg.get("appearance_weight") or 0.5
        self.velocity_gate_boost = tracking_cfg.get("velocity_gate_boost", 1.5)
        self.base_gate_multiplier = tracking_cfg.get("base_gate_multiplier", 1.0)
        self.use_appearance_in_tracking = tracking_cfg.get("use_appearance_in_tracking", True)
        self.stationary_cleanup_timeout = tracking_cfg.get("stationary_cleanup_timeout", 300)
        self.probation_threshold = tracking_cfg.get("probation_threshold", 3)
        self.occlusion_threshold = tracking_cfg.get("occlusion_threshold", 0.7)
        # T1: Max track limit to prevent O(n^2) cost matrix blowup
        self.max_active_tracks = vd_cfg.get("max_active_tracks", tracking_cfg.get("max_active_tracks", 50))
        # R3: Configurable ReID EMA alpha for embedding update
        self.embedding_ema_alpha = tracking_cfg.get("embedding_ema_alpha", 0.1)
        
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
        
        # Store base Q for adaptive scaling
        self._base_Q = kf.Q.copy()
        
        return kf

    def update(self, detections: List[Tuple], current_time: float, frame_shape: Tuple[int, int], skip_factor: int = 0) -> Dict[str, Dict]:
        """Runs the tracking association pipeline (ByteTrack based)."""
        new_or_updated_tracks = {}
        h, w = frame_shape[:2]
        
        # Dynamic Configuration parameters (allow updates from CoreModule)
        tracking_cfg = self.config.get("tracking", {})
        vd_cfg = self.config.get("vehicle_detection", {})
        
        # Adaptive Proximity: Increase search radius if frames were skipped
        # base threshold is for adjacent frames.
        base_proximity = tracking_cfg.get("proximity_threshold") or vd_cfg.get("proximity_threshold") or 250
        self.proximity_threshold = base_proximity * (1.0 + (skip_factor * 0.5))
        
        self.track_timeout = tracking_cfg.get("track_timeout") or vd_cfg.get("track_timeout") or 30
        self.probation_threshold = tracking_cfg.get("probation_threshold") or vd_cfg.get("probation_threshold") or 3
        
        # Use FeedConfig specific static object filter settings if present
        static_filter_enabled = self.config.get("static_object_filter_enabled", True)
        static_timeout = self.config.get("static_object_timeout", self.stationary_cleanup_timeout)
        
        # 1. Separate detections
        high_conf_dets = []
        low_conf_dets = []
        
        vd_cfg = self.config.get("vehicle_detection", {})
        CONF_THRESH = vd_cfg.get("confidence_threshold", self.config.get("confidence_threshold", 0.3))
        LOW_CONF_THRESH = vd_cfg.get("low_confidence_threshold", 0.1)
        
        for det in detections:
            bbox, cls, conf, emb = det
            if conf >= CONF_THRESH:
                high_conf_dets.append(det)
            elif conf >= LOW_CONF_THRESH:
                low_conf_dets.append(det)

        # 2. Kalman Prediction (with adaptive process noise)
        for tid, track in self.vehicle_data.items():
            kf = track.get("kalman_filter")
            if kf:
                dt = current_time - track.get("last_prediction_time", track["last_seen"])
                if dt <= 0.001: dt = 1.0 / self.fps
                
                kf.F[0, 4] = dt
                kf.F[1, 5] = dt
                
                # Adaptive Q: scale process noise by estimated speed
                speed = math.sqrt(float(kf.x[4][0])**2 + float(kf.x[5][0])**2)
                # T6: Scale noise by dt/skip factor too - large gaps mean more uncertainty
                dt_factor = max(1.0, dt * self.fps / 2.0)
                speed_factor = max(0.1, min(5.0, (speed / 100.0) * dt_factor))
                kf.Q = self._base_Q * speed_factor
                
                kf.predict()
                
                tx, ty, tw, th = kf.x[0][0], kf.x[1][0], kf.x[2][0], kf.x[3][0]
                track["predicted_bbox"] = (tx - tw/2, ty - th/2, tx + tw/2, ty + th/2)
                track["last_prediction_time"] = current_time

        # 3. First Association: High Confidence (IoU + ReID)
        matched_tracks_1 = set()
        matched_dets_1 = set()
        
        if high_conf_dets and self.vehicle_data:
            track_pool = list(self.vehicle_data.values())
            # T7: pass skip_factor to cost calculation for looser gating
            cost_matrix_1 = self._calculate_cost_matrix(high_conf_dets, track_pool, use_reid=True, skip_factor=skip_factor)
            
            if cost_matrix_1.size > 0:
                row_ind, col_ind = linear_sum_assignment(cost_matrix_1)
                for r, c in zip(row_ind, col_ind):
                    match_thresh = self.dynamic_matching_threshold * (1.0 + min(1.0, skip_factor * 0.1))
                    if cost_matrix_1[r, c] < match_thresh:
                        track = track_pool[c]
                        self._update_track(track, high_conf_dets[r], current_time)
                        matched_tracks_1.add(track["vehicle_id"])
                        matched_dets_1.add(r)
                        new_or_updated_tracks[track["vehicle_id"]] = track
                    else:
                        logger.debug(f"Association rejected: cost {cost_matrix_1[r, c]:.2f} > threshold {match_thresh:.2f}")

        unmatched_dets_1 = [d for i, d in enumerate(high_conf_dets) if i not in matched_dets_1]
        unmatched_tracks_1 = [t for tid, t in self.vehicle_data.items() if tid not in matched_tracks_1]

        # 4. Second Association: Low Confidence (IoU Only)
        matched_tracks_2 = set()
        if low_conf_dets and unmatched_tracks_1:
            cost_matrix_2 = self._calculate_cost_matrix(low_conf_dets, unmatched_tracks_1, use_reid=False, skip_factor=skip_factor)
            second_pass_thresh = self.config.get("tracking", {}).get("second_pass_threshold", 0.5)
            if cost_matrix_2.size > 0:
                row_ind, col_ind = linear_sum_assignment(cost_matrix_2)
                for r, c in zip(row_ind, col_ind):
                    if cost_matrix_2[r, c] < second_pass_thresh:
                        track = unmatched_tracks_1[c]
                        self._update_track(track, low_conf_dets[r], current_time)
                        matched_tracks_2.add(track["vehicle_id"])
                        new_or_updated_tracks[track["vehicle_id"]] = track

        # T9: Third Association: ReID Recovery for lost mature tracks
        # If skip is high, IoU often fails. Try matching by ReID alone for mature tracks.
        unmatched_dets_final = [d for i, d in enumerate(high_conf_dets) if i not in matched_dets_1]
        unmatched_tracks_final = [t for t in unmatched_tracks_1 if t["vehicle_id"] not in matched_tracks_2]
        
        if unmatched_dets_final and unmatched_tracks_final and skip_factor > 2:
            # ReID only match for high confidence detections and mature lost tracks
            mature_lost = [t for t in unmatched_tracks_final if t.get("status") == "active"]
            if mature_lost:
                cost_reid = self._calculate_cost_matrix(unmatched_dets_final, mature_lost, use_reid=True, reid_only=True)
                if cost_reid.size > 0:
                    row_ind, col_ind = linear_sum_assignment(cost_reid)
                    for r, c in zip(row_ind, col_ind):
                        # Use a stricter threshold for ReID-only recovery
                        if cost_reid[r, c] < 0.4:
                            track = mature_lost[c]
                            self._update_track(track, unmatched_dets_final[r], current_time)
                            new_or_updated_tracks[track["vehicle_id"]] = track
                            # Remove from unmatched pool
                            matched_tracks_1.add(track["vehicle_id"]) 

        # 5. Finalize matches and handle lost
        final_matched_all = matched_tracks_1.union(matched_tracks_2)
        for tid, track in self.vehicle_data.items():
            if tid not in final_matched_all:
                # Tentative tracks that are missed are immediately dropped
                if track.get("status") == "tentative":
                    continue
                    
                if (current_time - track["last_seen"]) < self.track_timeout:
                    track["status"] = "predicting"
                    if "predicted_bbox" in track:
                        track["bbox"] = track["predicted_bbox"]
                        # Clip to frame
                        px1, py1, px2, py2 = track["bbox"]
                        if px2 < 0 or px1 > w or py2 < 0 or py1 > h: continue
                        new_or_updated_tracks[tid] = track

        # 6. Initialize New Tracks (T1: enforce max_active_tracks ceiling)
        active_count = len(new_or_updated_tracks)
        for det in unmatched_dets_1:
            if active_count >= self.max_active_tracks:
                logger.debug(f"Max active tracks ({self.max_active_tracks}) reached, skipping new track creation")
                break
            new_track = self._create_new_track(det, current_time)
            new_or_updated_tracks[new_track["vehicle_id"]] = new_track
            active_count += 1
            
        # 7. Stationary Cleanup
        final_active_tracks = {}
        for tid, track in new_or_updated_tracks.items():
            if "start_centroid" not in track:
                track["start_centroid"] = track["centroid"]
                track["first_seen"] = current_time
            
            displacement = math.sqrt((track["centroid"][0] - track["start_centroid"][0])**2 + 
                                     (track["centroid"][1] - track["start_centroid"][1])**2)
            
            age = current_time - track["first_seen"]
            
            # T5 Fix: Only apply static object filter when explicitly enabled
            if static_filter_enabled and age > static_timeout and displacement < 50:
                continue
            
            final_active_tracks[tid] = track

        self.vehicle_data = final_active_tracks
        self._check_occlusions()
        
        # Run behavior analytics
        for tid, track in self.vehicle_data.items():
            if track["status"] == "active":
                self._classify_behavior(track)
                
        return self.vehicle_data

    def _calculate_cost_matrix(self, detections, tracks, use_reid=True, skip_factor=0, reid_only=False):
        num_dets = len(detections)
        num_tracks = len(tracks)
        if num_dets == 0 or num_tracks == 0:
            return np.empty((num_dets, num_tracks))
            
        costs = np.full((num_dets, num_tracks), 10000.0)
        
        reid_sim_matrix = None
        if use_reid:
            # Find an embedding dimension if possible
            emb_dim = 128
            for d in detections:
                if d[3] is not None:
                    emb_dim = len(d[3])
                    break
            
            det_embs = np.zeros((num_dets, emb_dim), dtype=np.float32)
            track_embs = np.zeros((num_tracks, emb_dim), dtype=np.float32)
            
            has_valid_det = False
            for d, det in enumerate(detections):
                if det[3] is not None:
                    det_embs[d] = det[3]
                    has_valid_det = True
                    
            has_valid_trk = False
            for t, track in enumerate(tracks):
                emb = track.get("embedding")
                if emb is not None:
                    track_embs[t] = emb
                    has_valid_trk = True
                    
            if has_valid_det and has_valid_trk:
                # O(1) vectorized operation instead of O(M*N) dot products
                reid_sim_matrix = np.dot(det_embs, track_embs.T)
        
        for d, (det_bbox, det_cls, det_conf, det_emb) in enumerate(detections):
            det_cx = (det_bbox[0] + det_bbox[2]) / 2
            det_cy = (det_bbox[1] + det_bbox[3]) / 2
            
            for t, track in enumerate(tracks):
                if reid_only:
                    if reid_sim_matrix is not None and det_emb is not None and track.get("embedding") is not None:
                        # ReID-only cost: 1.0 - similarity
                        costs[d, t] = 1.0 - reid_sim_matrix[d, t]
                    continue

                if "predicted_bbox" in track:
                    diou = self._bbox_diou(det_bbox, track["predicted_bbox"])
                    tr_cx = (track["predicted_bbox"][0] + track["predicted_bbox"][2]) / 2
                    tr_cy = (track["predicted_bbox"][1] + track["predicted_bbox"][3]) / 2
                    dist = math.sqrt((det_cx - tr_cx)**2 + (det_cy - tr_cy)**2)
                    
                    # Adaptive Gate: scale search radius by skip_factor
                    gate_dist = 150 * (1.0 + (skip_factor * 0.5))
                    
                    if diou > -0.5 or dist < gate_dist: # Gate
                        motion_cost = 1.0 - diou
                        reid_cost = 0.0
                        if reid_sim_matrix is not None and det_emb is not None and track.get("embedding") is not None:
                            # Re-scale ReID to match motion cost magnitude using precomputed sim
                            reid_cost = (1.0 - reid_sim_matrix[d, t]) * self.appearance_weight * 2.0
                        
                        costs[d, t] = motion_cost + reid_cost
        return costs

    def _update_track(self, track, det, current_time):
        bbox, cls, conf, emb = det
        prev_centroid = track["centroid"]
        track["bbox"] = bbox
        track["centroid"] = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
        
        # Update embedding with EMA if available (R3: configurable alpha)
        if emb is not None:
            if track.get("embedding") is None:
                track["embedding"] = emb
            else:
                # Confidence-weighted EMA: high-conf detections update more aggressively
                alpha = self.embedding_ema_alpha * min(1.0, conf / 0.7)
                track["embedding"] = alpha * emb + (1 - alpha) * track["embedding"]
                # Re-normalize
                norm = np.linalg.norm(track["embedding"])
                if norm > 0:
                    track["embedding"] /= norm
        
        # --- Velocity Bootstrapping ---
        # If track is young (<= 5 frames), boost velocity estimate using simple displacement
        # This helps the KF converge faster than waiting for Q/R to settle
        track_age = len(track.get("class_history", []))
        if 1 <= track_age <= 5:
            dt = current_time - track.get("last_seen", current_time)
            if dt > 0.001:
                prev_cx, prev_cy = prev_centroid
                curr_cx, curr_cy = track["centroid"]
                vx = (curr_cx - prev_cx) / dt
                vy = (curr_cy - prev_cy) / dt
                
                kf = track.get("kalman_filter")
                if kf:
                    # State: [x, y, w, h, vx, vy, vw, vh]
                    kf.x[4][0] = vx
                    kf.x[5][0] = vy
                    # T3 Fix: Inflate velocity covariance so KF doesn't over-trust
                    # the raw displacement-based estimate in early frames
                    kf.P[4, 4] = max(kf.P[4, 4], 50.0)
                    kf.P[5, 5] = max(kf.P[5, 5], 50.0)
                    
        track["last_seen"] = current_time
        
        # --- Probation Logic ---
        track["hits"] = track.get("hits", 0) + 1
        if track["hits"] >= self.probation_threshold:
            track["status"] = "active"
        # If not yet active, status remains 'tentative' (as set in _create_new_track)
        
        track["confidence"] = conf
        
        # --- Class Stabilization ---
        # Instead of instant switching, use voting window
        if "class_history" not in track:
            track["class_history"] = deque([track["class_id"]], maxlen=10)
        track["class_history"].append(cls)
        
        # T4 Fix: Majority vote with supermajority rule (60% threshold, min 3 votes)
        counts = Counter(track["class_history"])
        most_common = counts.most_common(1)
        if most_common:
            winner, count = most_common[0]
            total = len(track["class_history"])
            # Switch if we have at least 3 votes AND a 60% supermajority
            if count >= 3 and (count / total) >= 0.6:
                track["class_id"] = winner

        # Update Kalman
        kf = track.get("kalman_filter")
        innovation_mag = 0.0
        if kf:
            cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
            
            # Calculate innovation (residual) before update for quality scoring
            # z = [cx, cy, w, h]
            z = np.array([[cx], [cy], [w], [h]])
            innovation = z - np.dot(kf.H, kf.x)
            innovation_mag = float(np.linalg.norm(innovation[:2])) # only pos innovation
            
            kf.update(z)
            
            # Update history for behavior analytics
            track["position_history"].append((cx, cy))
            track["velocity_history"].append(((kf.x[4][0], kf.x[5][0]), current_time))

        self._update_quality_score(track, conf, innovation_mag)

    def _create_new_track(self, det, current_time):
        bbox, cls, conf, emb = det
        self.global_id_counter += 1
        # Use a combination of counter and short UUID to avoid collisions on reload
        track_id = f"TRK_{self.global_id_counter}_{uuid.uuid4().hex[:4].upper()}"
        return {
            "vehicle_id": track_id,
            "bbox": bbox,
            "centroid": ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2),
            "class_id": cls,
            "confidence": conf,
            "last_seen": current_time,
            "status": "tentative",
            "hits": 1,
            "kalman_filter": self._init_kalman((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2, bbox[2]-bbox[0], bbox[3]-bbox[1]),
            "embedding": emb,
            "class_history": deque([cls], maxlen=10),
            "speed_history": deque(maxlen=5),
            "position_history": deque(maxlen=20),
            "velocity_history": deque(maxlen=20),
            "plate_candidates": deque(maxlen=10),
            "acceleration": 0.0,
            "behavior": "normal",
            "quality_score": 1.0,
            "smoothed_speed": 0.0,
            "speed": 0.0,
            "last_speed_update_time": None,
        }

    def _bbox_giou(self, boxA, boxB):
        xA, yA, xB, yB = max(boxA[0], boxB[0]), max(boxA[1], boxB[1]), min(boxA[2], boxB[2]), min(boxA[3], boxB[3])
        inter = max(0, xB - xA) * max(0, yB - yA)
        areaA = max(0, boxA[2] - boxA[0]) * max(0, boxA[3] - boxA[1])
        areaB = max(0, boxB[2] - boxB[0]) * max(0, boxB[3] - boxB[1])
        union = areaA + areaB - inter
        if union <= 0:
            return 0.0
        iou = inter / (union + 1e-6)
        ex, ey, ex2, ey2 = min(boxA[0], boxB[0]), min(boxA[1], boxB[1]), max(boxA[2], boxB[2]), max(boxA[3], boxB[3])
        e_area = (ex2 - ex) * (ey2 - ey)
        if e_area <= 0:
            return iou
        return iou - (e_area - union) / (e_area + 1e-6)

    def _bbox_diou(self, boxA, boxB):
        """Distance-IoU: adds centroid distance penalty for better lane-parallel discrimination."""
        xA, yA, xB, yB = max(boxA[0], boxB[0]), max(boxA[1], boxB[1]), min(boxA[2], boxB[2]), min(boxA[3], boxB[3])
        inter = max(0, xB - xA) * max(0, yB - yA)
        areaA = max(0, boxA[2] - boxA[0]) * max(0, boxA[3] - boxA[1])
        areaB = max(0, boxB[2] - boxB[0]) * max(0, boxB[3] - boxB[1])
        union = areaA + areaB - inter
        if union <= 0:
            return 0.0
        iou = inter / (union + 1e-6)
        
        # Centroid distance
        cxA = (boxA[0] + boxA[2]) / 2
        cyA = (boxA[1] + boxA[3]) / 2
        cxB = (boxB[0] + boxB[2]) / 2
        cyB = (boxB[1] + boxB[3]) / 2
        d2 = (cxA - cxB)**2 + (cyA - cyB)**2
        
        # Enclosing box diagonal
        ex, ey = min(boxA[0], boxB[0]), min(boxA[1], boxB[1])
        ex2, ey2 = max(boxA[2], boxB[2]), max(boxA[3], boxB[3])
        c2 = (ex2 - ex)**2 + (ey2 - ey)**2
        if c2 <= 0:
            return iou
        
        return iou - d2 / (c2 + 1e-6)

    def _bbox_iou(self, boxA, boxB):
        xA, yA, xB, yB = max(boxA[0], boxB[0]), max(boxA[1], boxB[1]), min(boxA[2], boxB[2]), min(boxA[3], boxB[3])
        inter = max(0, xB - xA) * max(0, yB - yA)
        areaA = max(0, boxA[2] - boxA[0]) * max(0, boxA[3] - boxA[1])
        areaB = max(0, boxB[2] - boxB[0]) * max(0, boxB[3] - boxB[1])
        union = areaA + areaB - inter
        if union <= 0:
            return 0.0
        return inter / union

    def _check_occlusions(self):
        """Identifies tracks that are likely occluded by each other."""
        tids = list(self.vehicle_data.keys())
        # Reset occlusion status
        for tid in tids:
            self.vehicle_data[tid]["is_occluded"] = False

        for i in range(len(tids)):
            for j in range(i + 1, len(tids)):
                t1 = self.vehicle_data[tids[i]]
                t2 = self.vehicle_data[tids[j]]
                
                b1 = t1.get("predicted_bbox") or t1["bbox"]
                b2 = t2.get("predicted_bbox") or t2["bbox"]
                
                iou = self._bbox_iou(b1, b2)
                if iou > self.occlusion_threshold:
                    t1["is_occluded"] = True
                    t2["is_occluded"] = True

    def _classify_behavior(self, track: Dict):
        """Classifies vehicle behavior (hard braking, turning) based on history."""
        v_hist = list(track["velocity_history"])
        if len(v_hist) < 5:
            return

        # 1. Acceleration (change in speed) - Smoothed over window
        # Use a 5-frame window if available
        window = 5
        if len(v_hist) < window:
             window = len(v_hist)
        
        (vx_start, vy_start), t_start = v_hist[-window]
        (vx_end, vy_end), t_end = v_hist[-1]
        
        s_start = math.sqrt(vx_start**2 + vy_start**2)
        s_end = math.sqrt(vx_end**2 + vy_end**2)
        
        # Exact time delta from timestamps
        dt = t_end - t_start
        if dt <= 0.001: 
            # Fallback if timestamps are identical (should not happen with actual frames)
            dt = (window - 1) * (1.0 / self.fps)
        
        accel = (s_end - s_start) / dt
        track["acceleration"] = round(accel, 2)

        # 2. Behavior Classification
        behavior = "normal"
        
        # Hard Braking detection
        # Threshold: Significant negative acceleration
        # e.g., dropping 100 px/s in 1 second -> -100
        # Check if speed is significant to avoid noise at near-zero speeds
        if s_start > 50 and accel < -150: 
            behavior = "hard_braking"
        
        # Turning detection
        if len(v_hist) >= 10:
            # Check angle change between start and end of window
            (vx_initial, vy_initial), _ = v_hist[-10]
            (vx_final, vy_final), _ = v_hist[-1]
            
            # Only check angle if moving
            if (vx_initial**2 + vy_initial**2) > 100 and (vx_final**2 + vy_final**2) > 100:
                angle_start = math.atan2(vy_initial, vx_initial)
                angle_end = math.atan2(vy_final, vx_final)
                angle_diff = abs(angle_end - angle_start)
                # Normalize angle diff
                if angle_diff > math.pi:
                    angle_diff = 2*math.pi - angle_diff
                
                if angle_diff > math.pi / 6: # > 30 degrees
                    behavior = "turning"
        
        # Stationary detection (if smoothed speed is very low but hits are high)
        if s_end < 10 and track["hits"] > 30:
            behavior = "stationary"

        track["behavior"] = behavior

    def _update_quality_score(self, track: Dict, conf: float, kf_innovation: Optional[float] = None):
        """Refines the track quality score based on surprise and confidence."""
        prev_q = track.get("quality_score", 1.0)
        
        # 1. Base confidence component (40%)
        conf_score = conf
        
        # 2. Innovation component (Surprise) (40%)
        # Lower surprise (innovation) -> higher quality
        # normalized_innovation: 0 (perfect match) to 1+ (high surprise)
        inn_score = 1.0
        if kf_innovation is not None:
            # Heuristic mapping: innovation > 100 pixels is "very high surprise"
            inn_score = max(0, 1.0 - (kf_innovation / 150.0))
            
        # 3. Occlusion/Predicting penalty (20%)
        occ_penalty = 1.0
        if track.get("is_occluded"):
            occ_penalty = 0.7
        elif track.get("status") == "predicting":
            occ_penalty = 0.5
            
        # Update using EWMA
        instant_q = (conf_score * 0.4 + inn_score * 0.4 + occ_penalty * 0.2)
        track["quality_score"] = round(0.1 * instant_q + 0.9 * prev_q, 3)
