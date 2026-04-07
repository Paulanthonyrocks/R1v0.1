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
        
        tracking_cfg = config.get("tracking", {})
        vd_cfg = config.get("vehicle_detection", {})
        self.proximity_threshold = tracking_cfg.get("proximity_threshold") or vd_cfg.get("proximity_threshold") or 250
        self.track_timeout = tracking_cfg.get("track_timeout") or vd_cfg.get("track_timeout") or 30
        self.dynamic_matching_threshold = tracking_cfg.get("dynamic_matching_threshold", 0.7)
        self.appearance_weight = tracking_cfg.get("appearance_weight") or 0.5
        self.use_appearance_in_tracking = tracking_cfg.get("use_appearance_in_tracking", True)
        self.probation_threshold = tracking_cfg.get("probation_threshold", 3)
        self.occlusion_threshold = tracking_cfg.get("occlusion_threshold", 0.7)
        self.max_active_tracks = vd_cfg.get("max_active_tracks", tracking_cfg.get("max_active_tracks", 50))
        self.embedding_ema_alpha = tracking_cfg.get("embedding_ema_alpha", 0.1)
        
        self.global_id_counter = 0

    def _init_kalman(self, cx, cy, w, h, dt_init=None):
        kf = KalmanFilter(dim_x=8, dim_z=4)
        # Use dynamic dt if provided, else default to static FPS
        dt = dt_init if dt_init is not None else 1.0 / self.fps
        kf.x = np.array([[cx], [cy], [w], [h], [0], [0], [0], [0]])
        kf.F = np.eye(8)
        kf.F[0, 4] = dt
        kf.F[1, 5] = dt
        kf.F[2, 6] = dt
        kf.F[3, 7] = dt
        kf.H = np.array([[1,0,0,0,0,0,0,0], [0,1,0,0,0,0,0,0], [0,0,1,0,0,0,0,0], [0,0,0,1,0,0,0,0]])
        kf.P *= 10.0
        kf.R *= 1.0
        kf.Q *= 0.1
        self._base_Q = kf.Q.copy()
        return kf

    def update(self, detections: List[Tuple], current_time: float, frame_shape: Tuple[int, int], skip_factor: int = 0) -> Dict[str, Dict]:
        new_or_updated_tracks = {}
        h, w = frame_shape[:2]
        
        tracking_cfg = self.config.get("tracking", {})
        vd_cfg = config.get("vehicle_detection", {})
        self.track_timeout = tracking_cfg.get("track_timeout") or vd_cfg.get("track_timeout") or 30
        self.probation_threshold = tracking_cfg.get("probation_threshold") or vd_cfg.get("probation_threshold") or 3
        
        high_conf_dets, low_conf_dets = [], []
        CONF_THRESH, LOW_CONF_THRESH = vd_cfg.get("confidence_threshold", 0.3), vd_cfg.get("low_confidence_threshold", 0.1)
        for det in detections: 
            if det[2] >= CONF_THRESH: high_conf_dets.append(det)
            elif det[2] >= LOW_CONF_THRESH: low_conf_dets.append(det)

        # BUG #14 FIX: Apply dynamic time delta to Kalman filter predictions
        dt = (1.0 + skip_factor) / self.fps
        for track in self.vehicle_data.values():
            if kf := track.get("kalman_filter"): 
                # Update transition matrix with real time delta
                kf.F[0, 4] = dt
                kf.F[1, 5] = dt
                kf.F[2, 6] = dt
                kf.F[3, 7] = dt
                kf.predict()
                tx, ty, tw, th = kf.x[0,0], kf.x[1,0], kf.x[2,0], kf.x[3,0]
                track["predicted_bbox"] = (tx - tw/2, ty - th/2, tx + tw/2, ty + th/2)

        matched_tracks_1, matched_dets_1 = self._associate(high_conf_dets, list(self.vehicle_data.values()), new_or_updated_tracks, current_time)
        unmatched_dets_1 = [d for i, d in enumerate(high_conf_dets) if i not in matched_dets_1]
        unmatched_tracks_1 = [t for t in self.vehicle_data.values() if t["vehicle_id"] not in matched_tracks_1]

        # BUG #16 FIX: Correctly filter out tracks matched in Phase 2
        matched_tracks_2, _ = self._associate(low_conf_dets, unmatched_tracks_1, new_or_updated_tracks, current_time, use_reid=False)
        unmatched_tracks_2 = [t for t in unmatched_tracks_1 if t["vehicle_id"] not in matched_tracks_2]
        
        reid_matched_det_indices = set()
        if unmatched_dets_1 and unmatched_tracks_2:
            mature_lost = [t for t in unmatched_tracks_2 if t.get("status") == "active"]
            if mature_lost:
                cost_reid = self._calculate_cost_matrix(unmatched_dets_1, mature_lost, use_reid=True, reid_only=True)
                if cost_reid.size > 0:
                    row_ind, col_ind = linear_sum_assignment(cost_reid)
                    for r, c in zip(row_ind, col_ind):
                        if cost_reid[r, c] < 0.4: # Stricter threshold for ReID-only matching
                            self._update_track(mature_lost[c], unmatched_dets_1[r], current_time)
                            new_or_updated_tracks[mature_lost[c]["vehicle_id"]] = mature_lost[c]
                            reid_matched_det_indices.add(r)

        # Update status of remaining lost tracks
        for track in unmatched_tracks_2:
            if (current_time - track["last_seen"]) < self.track_timeout:
                track["status"] = "predicting"
                if "predicted_bbox" in track: 
                    track["bbox"] = np.clip(track["predicted_bbox"], [0, 0, 0, 0], [w, h, w, h])
                    new_or_updated_tracks[track["vehicle_id"]] = track

        # Create new tracks for unmatched high-confidence detections
        for i, det in enumerate(unmatched_dets_1):
            if i not in reid_matched_det_indices and len(new_or_updated_tracks) < self.max_active_tracks:
                new_track = self._create_new_track(det, current_time, dt_init=dt)
                new_or_updated_tracks[new_track["vehicle_id"]] = new_track

        self.vehicle_data = new_or_updated_tracks
        return self.vehicle_data

    def _associate(self, detections, tracks, new_or_updated_tracks, current_time, use_reid=True):
        matched_tracks, matched_dets = set(), set()
        if not detections or not tracks: return matched_tracks, matched_dets
        cost_matrix = self._calculate_cost_matrix(detections, tracks, use_reid=use_reid)
        if cost_matrix.size > 0:
            row_ind, col_ind = linear_sum_assignment(cost_matrix)
            for r, c in zip(row_ind, col_ind):
                if cost_matrix[r, c] < self.dynamic_matching_threshold:
                    track = tracks[c]
                    self._update_track(track, detections[r], current_time)
                    new_or_updated_tracks[track["vehicle_id"]] = track
                    matched_tracks.add(track["vehicle_id"])
                    matched_dets.add(r)
        return matched_tracks, matched_dets

    def _calculate_cost_matrix(self, detections, tracks, use_reid=True, reid_only=False):
        num_dets, num_tracks = len(detections), len(tracks)
        if num_dets == 0 or num_tracks == 0: return np.empty((0,0))
            
        costs = np.full((num_dets, num_tracks), 1e5)
        det_embs = np.array([d[3] for d in detections if d[3] is not None])
        track_embs = np.array([t["embedding"] for t in tracks if t.get("embedding") is not None])

        use_reid = use_reid and self.use_appearance_in_tracking and det_embs.size > 0 and track_embs.size > 0

        if use_reid:
            # BUG #17 FIX: Normalize both detection and track embeddings before dot product
            det_norms = np.linalg.norm(det_embs, axis=1, keepdims=True)
            track_norms = np.linalg.norm(track_embs, axis=1, keepdims=True)
            det_embs_norm = np.divide(det_embs, det_norms, out=np.zeros_like(det_embs), where=det_norms!=0)
            track_embs_norm = np.divide(track_embs, track_norms, out=np.zeros_like(track_embs), where=track_norms!=0)
            reid_sim_matrix = np.dot(det_embs_norm, track_embs_norm.T)

        for d_idx, (det_bbox, _, _, _) in enumerate(detections):
            for t_idx, track in enumerate(tracks):
                motion_cost = 1.0 - self._bbox_diou(det_bbox, track.get("predicted_bbox", track["bbox"]))
                
                reid_cost = 0.0
                if use_reid and detections[d_idx][3] is not None and track.get("embedding") is not None:
                    # Find the correct index in the sim matrix (this is complex)
                    try:
                        det_sim_idx = np.where((det_embs == detections[d_idx][3]).all(axis=1))[0][0]
                        track_sim_idx = np.where((track_embs == track["embedding"]).all(axis=1))[0][0]
                        sim = reid_sim_matrix[det_sim_idx, track_sim_idx]
                        reid_cost = (1.0 - sim) * self.appearance_weight
                    except IndexError:
                        # This can happen if an embedding is None or not found, fall back to motion only
                        pass
                
                if reid_only:
                    costs[d_idx, t_idx] = 1.0 - sim if 'sim' in locals() else 1.0
                else:
                    costs[d_idx, t_idx] = motion_cost * (1 - self.appearance_weight) + reid_cost

        return costs

    def _update_track(self, track, det, current_time):
        bbox, cls, conf, emb = det
        track["bbox"] = bbox
        track["centroid"] = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
        
        if emb is not None and track.get("embedding") is not None: 
            track["embedding"] = self.embedding_ema_alpha * emb + (1 - self.embedding_ema_alpha) * track["embedding"]
            track["embedding"] /= np.linalg.norm(track["embedding"])
        elif emb is not None: 
            track["embedding"] = emb / np.linalg.norm(emb)
        
        track["last_seen"] = current_time
        track["hits"] = track.get("hits", 0) + 1
        if track["hits"] >= self.probation_threshold: track["status"] = "active"
        track["confidence"] = conf

        kf = track.get("kalman_filter");
        if kf: kf.update(np.array([[(bbox[0]+bbox[2])/2], [(bbox[1]+bbox[3])/2], [bbox[2]-bbox[0]], [bbox[3]-bbox[1]]]))

    def _create_new_track(self, det, current_time, dt_init=None):
        bbox, cls, conf, emb = det
        normalized_emb = emb / np.linalg.norm(emb) if emb is not None else None
        cx, cy, w, h = (bbox[0]+bbox[2])/2, (bbox[1]+bbox[3])/2, bbox[2]-bbox[0], bbox[3]-bbox[1]
        return {"vehicle_id": uuid.uuid4().hex, "bbox": bbox, "centroid": (cx, cy), "class_id": cls, "confidence": conf, "last_seen": current_time, "status": "tentative", "hits": 1, "kalman_filter": self._init_kalman(cx, cy, w, h, dt_init=dt_init), "embedding": normalized_emb}

    def _bbox_diou(self, boxA, boxB):
        inter = max(0, min(boxA[2], boxB[2]) - max(boxA[0], boxB[0])) * max(0, min(boxA[3], boxB[3]) - max(boxA[1], boxB[1]))
        union = max(0, boxA[2]-boxA[0])*max(0, boxA[3]-boxA[1]) + max(0, boxB[2]-boxB[0])*max(0, boxB[3]-boxB[1]) - inter
        iou = inter / (union + 1e-6)
        d2 = ((boxA[0]+boxA[2])/2 - (boxB[0]+boxB[2])/2)**2 + ((boxA[1]+boxA[3])/2 - (boxB[1]+boxB[3])/2)**2
        c2 = (max(boxA[2],boxB[2]) - min(boxA[0],boxB[0]))**2 + (max(boxA[3],boxB[3]) - min(boxA[1],boxB[1]))**2
        return iou - d2/(c2 + 1e-6)