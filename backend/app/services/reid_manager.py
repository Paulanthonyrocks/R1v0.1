import time
import numpy as np
import pickle
import os
from typing import Dict, List, Optional
import logging
from threading import Lock

logger = logging.getLogger("app.services.reid")

class GlobalReIDManager:
    def __init__(self, config: dict):
        self.config = config
        self.reid_cfg = config.get("reid", {})
        self.similarity_threshold = self.reid_cfg.get("similarity_threshold", 0.85)
        self.max_gallery_size = self.reid_cfg.get("max_gallery_size", 1000)
        self.ttl_seconds = self.reid_cfg.get("ttl_seconds", 3600)
        self.persistence_path = self.reid_cfg.get("persistence_path", "backend/data/reid_gallery.pkl")
        
        # Thread safety lock
        self._lock = Lock()
        
        # Initialize internal state
        self.metadata_store: Dict[str, Dict] = {}
        self.local_to_global: Dict[str, Dict[str, str]] = {}
        self.global_counter = 1
        self.last_cleanup_time = 0
        self.gallery_ids: List[str] = []
        self.gallery_matrix: Optional[np.ndarray] = None 

        # Load existing state if available
        self.load_state()

    def save_state(self):
        """Persists the current gallery and mappings to disk."""
        try:
            os.makedirs(os.path.dirname(self.persistence_path), exist_ok=True)
            with open(self.persistence_path, 'wb') as f:
                state = {
                    "metadata_store": self.metadata_store,
                    "local_to_global": self.local_to_global,
                    "global_counter": self.global_counter,
                    "gallery_ids": self.gallery_ids,
                    "gallery_matrix": self.gallery_matrix
                }
                pickle.dump(state, f)
            logger.info(f"ReID state saved to {self.persistence_path}")
        except Exception as e:
            logger.error(f"Failed to save ReID state: {e}")

    def load_state(self):
        """Loads the gallery and mappings from disk."""
        if not os.path.exists(self.persistence_path):
            logger.info("No ReID persistence file found. Starting fresh.")
            return

        try:
            with open(self.persistence_path, 'rb') as f:
                state = pickle.load(f)
                self.metadata_store = state.get("metadata_store", {})
                self.local_to_global = state.get("local_to_global", {})
                self.global_counter = state.get("global_counter", 1)
                self.gallery_ids = state.get("gallery_ids", [])
                self.gallery_matrix = state.get("gallery_matrix")
            logger.info(f"ReID state loaded from {self.persistence_path}. Total IDs: {len(self.gallery_ids)}")
        except Exception as e:
            logger.error(f"Failed to load ReID state: {e}")

    def _normalize(self, vector: np.ndarray) -> np.ndarray:
        norm = np.linalg.norm(vector)
        return vector / norm if norm > 1e-6 else vector

    def get_global_id(self, feed_id: str, local_id: str) -> Optional[str]:
        with self._lock:
            if feed_id in self.local_to_global and local_id in self.local_to_global[feed_id]:
                return self.local_to_global[feed_id][local_id]
        return None

    def match_or_register(self, feed_id: str, local_id: str, embedding: np.ndarray, metadata: dict) -> str:
        # Normalize immediately
        embedding = self._normalize(embedding)
        now = time.time()

        with self._lock:
            # 1. Check local cache first (Fastest)
            if feed_id in self.local_to_global and local_id in self.local_to_global[feed_id]:
                gid = self.local_to_global[feed_id][local_id]
                if gid in self.metadata_store:
                    self.metadata_store[gid]["last_seen"] = now
                    # Optional: Update average embedding here (requires keeping state differently)
                    return gid

            # 2. Vectorized Search
            best_match_id = None
            best_score = -1.0

            if self.gallery_matrix is not None and len(self.gallery_ids) > 0:
                # One Matrix Multiplication for all candidates
                # shape: (N_gallery, Dim) . (Dim,) -> (N_gallery,)
                scores = np.dot(self.gallery_matrix, embedding)
                
                # Find best score
                best_idx = np.argmax(scores)
                best_score = scores[best_idx]

                if best_score > self.similarity_threshold:
                    best_match_id = self.gallery_ids[best_idx]
                    logger.debug(f"ReID Match: {local_id} -> {best_match_id} (Score: {best_score:.3f})")

            # 3. Register or Return
            if best_match_id:
                global_id = best_match_id
                self.metadata_store[global_id]["last_seen"] = now
            else:
                # Create New
                global_id = f"GLB_{self.global_counter}"
                self.global_counter += 1
                
                # Add to Matrix
                if self.gallery_matrix is None:
                    self.gallery_matrix = embedding.reshape(1, -1)
                else:
                    self.gallery_matrix = np.vstack([self.gallery_matrix, embedding])
                
                self.gallery_ids.append(global_id)
                self.metadata_store[global_id] = {
                    "last_seen": now,
                    "metadata": metadata
                }
                logger.info(f"ReID New: {local_id} -> {global_id}")

            # 4. Update Mappings
            if feed_id not in self.local_to_global:
                self.local_to_global[feed_id] = {}
            self.local_to_global[feed_id][local_id] = global_id

            # 5. Periodic Cleanup
            if now - self.last_cleanup_time > 60:
                self._cleanup(now)
            
            return global_id

    def _cleanup(self, now: float):
        """Removes old entries from Matrix, Metadata, and Mappings."""
        self.last_cleanup_time = now
        
        # Identify indices to keep
        keep_indices = []
        expired_gids = set()

        for idx, gid in enumerate(self.gallery_ids):
            last_seen = self.metadata_store.get(gid, {}).get("last_seen", 0)
            if now - last_seen <= self.ttl_seconds:
                keep_indices.append(idx)
            else:
                expired_gids.add(gid)
                if gid in self.metadata_store:
                    del self.metadata_store[gid]

        # Enforce Max Size (Remove oldest if full)
        if len(keep_indices) > self.max_gallery_size:
            # Sort keep_indices based on last_seen
            keep_indices.sort(key=lambda i: self.metadata_store[self.gallery_ids[i]]["last_seen"], reverse=True)
            # Trim
            indices_to_remove = keep_indices[self.max_gallery_size:]
            keep_indices = keep_indices[:self.max_gallery_size]
            
            for i in indices_to_remove:
                gid = self.gallery_ids[i]
                expired_gids.add(gid)
                del self.metadata_store[gid]

        # Rebuild Matrix and ID List
        if len(keep_indices) < len(self.gallery_ids):
            self.gallery_ids = [self.gallery_ids[i] for i in keep_indices]
            if self.gallery_ids:
                self.gallery_matrix = self.gallery_matrix[keep_indices]
            else:
                self.gallery_matrix = None
            logger.info(f"ReID Cleanup: Removed {len(expired_gids)} vehicles.")

            # CLEANUP MAPPINGS (Fixes the memory leak)
            for feed_id in list(self.local_to_global.keys()):
                # Filter dictionary inplace
                self.local_to_global[feed_id] = {
                    lid: gid for lid, gid in self.local_to_global[feed_id].items() 
                    if gid not in expired_gids
                }
                if not self.local_to_global[feed_id]:
                    del self.local_to_global[feed_id]