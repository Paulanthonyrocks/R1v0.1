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
        dt = dt_init if dt_init is not None else 1.0 / self.fps
        kf.x = np.array([[cx], [cy], [w], [h], [0], [0], [0], [0]])
        kf.F = np.array([[1,0,0,0,dt,0,0,0], [0,1,0,0,0,dt,0,0], [0,0,1,0,0,0,dt,0], [0,0,0,1,0,0,0,dt],
                         [0,0,0,0,1,0,0,0], [0,0,0,0,0,1,0,0], [0,0,0,0,0,0,1,0], [0,0,0,0,0,0,0,1]])
        kf.H = np.array([[1,0,0,0,0,0,0,0], [0,1,0,0,0,0,0,0], [0,0,1,0,0,0,0,0], [0,0,0,1,0,0,0,0]])
        kf.P *= 10.0
        kf.R *= 1.0
        kf.Q[4:,4:] *= 0.1 # Motion uncertainty
        return kf

    def update(self, detections: List[Tuple], dt: float, frame_shape: Tuple[int, int], skip_factor: int = 0) -> Dict[str, Dict]:
        new_or_updated_tracks = {}
        h, w = frame_shape[:2]
        
        tracking_cfg = self.config.get("tracking", {})
        vd_cfg = self.config.get("vehicle_detection", {})
        self.track_timeout = tracking_cfg.get("track_timeout", vd_cfg.get("track_timeout", 30))
        self.probation_threshold = tracking_cfg.get("probation_threshold", vd_cfg.get("probation_threshold", 3))
        
        CONF_THRESH, LOW_CONF_THRESH = vd_cfg.get("confidence_threshold", 0.3), vd_cfg.get("low_confidence_threshold", 0.1)
        high_conf_dets, low_conf_dets = [d for d in detections if d[2] >= CONF_THRESH], [d for d in detections if LOW_CONF_THRESH <= d[2] < CONF_THRESH]

        for track in self.vehicle_data.values():
            if kf := track.get("kalman_filter"): 
                kf.F[0, 4] = kf.F[1, 5] = kf.F[2, 6] = kf.F[3, 7] = dt
                kf.predict()
                tx, ty, tw, th = kf.x[0,0], kf.x[1,0], kf.x[2,0], kf.x[3,0]
                track["predicted_bbox"] = (tx - tw/2, ty - th/2, tx + tw/2, ty + th/2)
            track["age"] = track.get("age", 0) + dt

        matched_tracks_1, matched_dets_1 = self._associate(high_conf_dets, list(self.vehicle_data.values()), new_or_updated_tracks, dt)
        unmatched_dets_1 = [d for i, d in enumerate(high_conf_dets) if i not in matched_dets_1]
        unmatched_tracks_1 = [t for t in self.vehicle_data.values() if t["vehicle_id"] not in matched_tracks_1]

        matched_tracks_2, _ = self._associate(low_conf_dets, unmatched_tracks_1, new_or_updated_tracks, dt, use_reid=False)
        unmatched_tracks_2 = [t for t in unmatched_tracks_1 if t["vehicle_id"] not in matched_tracks_2]
        
        reid_matched_det_indices = set()
        if unmatched_dets_1 and unmatched_tracks_2 and (mature_lost := [t for t in unmatched_tracks_2 if t.get("status") == "active"]):
            cost_reid = self._calculate_cost_matrix(unmatched_dets_1, mature_lost, use_reid=True, reid_only=True)
            if cost_reid.size > 0:
                row_ind, col_ind = linear_sum_assignment(cost_reid)
                for r, c in zip(row_ind, col_ind):
                    if cost_reid[r, c] < 0.4:
                        self._update_track(mature_lost[c], unmatched_dets_1[r], dt)
                        new_or_updated_tracks[mature_lost[c]["vehicle_id"]] = mature_lost[c]
                        reid_matched_det_indices.add(r)

        for track in unmatched_tracks_2:
            if track["age"] < self.track_timeout:
                track["status"] = "predicting"
                if "predicted_bbox" in track: 
                    track["bbox"] = np.clip(track["predicted_bbox"], [0, 0, 0, 0], [w, h, w, h])
                    new_or_updated_tracks[track["vehicle_id"]] = track

        for i, det in enumerate(unmatched_dets_1):
            if i not in reid_matched_det_indices and len(new_or_updated_tracks) < self.max_active_tracks:
                new_track = self._create_new_track(det, dt)
                new_or_updated_tracks[new_track["vehicle_id"]] = new_track

        self.vehicle_data = new_or_updated_tracks
        return self.vehicle_data

    def _associate(self, detections, tracks, new_or_updated_tracks, dt, use_reid=True):
        matched_tracks, matched_dets = set(), set()
        if not detections or not tracks: return matched_tracks, matched_dets
        cost_matrix = self._calculate_cost_matrix(detections, tracks, use_reid=use_reid)
        if cost_matrix.size > 0:
            row_ind, col_ind = linear_sum_assignment(cost_matrix)
            for r, c in zip(row_ind, col_ind):
                if cost_matrix[r, c] < self.dynamic_matching_threshold:
                    track = tracks[c]
                    self._update_track(track, detections[r], dt)
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
            det_norms = np.linalg.norm(det_embs, axis=1, keepdims=True); det_embs_norm = det_embs / det_norms
            track_norms = np.linalg.norm(track_embs, axis=1, keepdims=True); track_embs_norm = track_embs / track_norms
            reid_sim_matrix = np.dot(det_embs_norm, track_embs_norm.T)

        for d_idx, (det_bbox, _, _, det_emb) in enumerate(detections):
            for t_idx, track in enumerate(tracks):
                motion_cost = 1.0 - self._bbox_diou(det_bbox, track.get("predicted_bbox", track["bbox"]))
                reid_cost = 0.0
                if use_reid and det_emb is not None and track.get("embedding") is not None:
                    try:
                        det_sim_idx = next(i for i, v in enumerate(det_embs) if np.array_equal(v, det_emb))
                        track_sim_idx = next(i for i, v in enumerate(track_embs) if np.array_equal(v, track["embedding"]))
                        sim = reid_sim_matrix[det_sim_idx, track_sim_idx]
                        reid_cost = (1.0 - sim) * self.appearance_weight
                    except StopIteration: pass
                if reid_only: costs[d_idx, t_idx] = 1.0 - sim if 'sim' in locals() else 1.0
                else: costs[d_idx, t_idx] = motion_cost * (1 - self.appearance_weight) + reid_cost
        return costs

    def _update_track(self, track, det, dt):
        bbox, cls, conf, emb = det
        track["bbox"] = bbox
        track["centroid"] = ((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
        if emb is not None: 
            current_emb = track.get("embedding")
            new_emb = self.embedding_ema_alpha * emb + (1 - self.embedding_ema_alpha) * current_emb if current_emb is not None else emb
            track["embedding"] = new_emb / np.linalg.norm(new_emb)
        track["age"] += dt
        track["hits"] = track.get("hits", 0) + 1
        if track["hits"] >= self.probation_threshold: track["status"] = "active"
        track["confidence"] = conf
        if kf := track.get("kalman_filter"): kf.update(np.array([[(bbox[0]+bbox[2])/2], [(bbox[1]+bbox[3])/2], [bbox[2]-bbox[0]], [bbox[3]-bbox[1]]]))

    def _create_new_track(self, det, dt):
        bbox, cls, conf, emb = det
        cx, cy, w, h = (bbox[0]+bbox[2])/2, (bbox[1]+bbox[3])/2, bbox[2]-bbox[0], bbox[3]-bbox[1]
        return {"vehicle_id": uuid.uuid4().hex, "bbox": bbox, "centroid": (cx, cy), "class_id": cls, "confidence": conf, "age": dt, "status": "tentative", "hits": 1, "kalman_filter": self._init_kalman(cx, cy, w, h, dt_init=dt), "embedding": emb / np.linalg.norm(emb) if emb is not None else None}

    def _bbox_diou(self, boxA, boxB):
        inter = max(0, min(boxA[2], boxB[2]) - max(boxA[0], boxB[0])) * max(0, min(boxA[3], boxB[3]) - max(boxA[1], boxB[1]))
        union = max(1e-6, (boxA[2]-boxA[0])*(boxA[3]-boxA[1]) + (boxB[2]-boxB[0])*(boxB[3]-boxB[1]) - inter)
        iou = inter / union
        d2 = ((boxA[0]+boxA[2])/2 - (boxB[0]+boxB[2])/2)**2 + ((boxA[1]+boxA[3])/2 - (boxB[1]+boxB[3])/2)**2
        c2 = (max(boxA[2],boxB[2]) - min(boxA[0],boxB[0]))**2 + (max(boxA[3],boxB[3]) - min(boxA[1],boxB[1]))**2
        return iou - d2/(c2 + 1e-6)
