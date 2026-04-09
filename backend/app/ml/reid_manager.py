import logging
import time
import threading
import numpy as np
from typing import Dict, List, Optional, Tuple
from collections import deque
import uuid

logger = logging.getLogger("app.ml.reid_manager")

class ReIDManager:
    """
    Manages Global Identities across multiple feeds.
    Uses embeddings to match local 'vehicle_id's to a persistent 'global_id'.
    """
    def __init__(self, config: dict):
        self.config = config
        self.reid_cfg = config.get("vehicle_detection", {}).get("reid", {})
        
        # Thread safety lock for gallery and mapping mutations
        self._lock = threading.RLock()
        
        # Identity Map: local_id -> global_id
        # We use a nested dict: {feed_id: {local_id: global_id}}
        self.local_to_global: Dict[str, Dict[str, str]] = {}
        
        # Global Gallery: global_id -> {embedding: np.ndarray, last_seen: float, metadata: dict}
        self.global_gallery: Dict[str, Dict] = {}
        
        # Similarity threshold for matching
        self.match_threshold = self.reid_cfg.get("match_threshold", 0.75)
        
        # Memory limit for gallery
        self.max_gallery_size = self.reid_cfg.get("max_gallery_size", 5000)
        self.gallery_timeout = self.reid_cfg.get("gallery_timeout", 3600) # 1 hour
        
        # Throttled cleanup timer
        self._last_cleanup_time = 0.0
        self._cleanup_interval = 60.0 # Run cleanup once per minute

    def register_vehicle(self, feed_id: str, local_id: str, embedding: np.ndarray, metadata: dict = None) -> str:
        """
        Attempts to match an embedding to an existing global identity.
        If no match found, creates a new global identity.
        """
        if embedding is None:
            return "unknown"

        now = time.time()
        
        with self._lock:
            # 1. Throttled Cleanup of old entries to avoid CPU thrashing in the hot path
            if now - self._last_cleanup_time > self._cleanup_interval:
                self._cleanup_gallery(now)
                self._last_cleanup_time = now

            # 2. Check if already known in this feed
            if feed_id in self.local_to_global and local_id in self.local_to_global[feed_id]:
                gid = self.local_to_global[feed_id][local_id]
                # Update embedding and time
                if gid in self.global_gallery:
                    # Weighted update of embedding (momentum)
                    old_emb = self.global_gallery[gid]["embedding"]
                    new_emb = 0.9 * old_emb + 0.1 * embedding
                    # Fix: Re-normalize the vector after momentum update to preserve cosine similarity math
                    self.global_gallery[gid]["embedding"] = new_emb / np.linalg.norm(new_emb)
                    self.global_gallery[gid]["last_seen"] = now
                return gid

            # 3. Search for match in global gallery
            best_gid = None
            max_sim = -1.0
            
            for gid, entry in self.global_gallery.items():
                # Basic cosine similarity (since embeddings are L2 normalized)
                sim = np.dot(embedding, entry["embedding"])
                if sim > max_sim:
                    max_sim = sim
                    best_gid = gid

            if max_sim > self.match_threshold:
                logger.info(f"ReID Match! Local {local_id}@{feed_id} matched Global {best_gid} (Sim: {max_sim:.3f})")
                gid = best_gid
                # Update
                old_emb = self.global_gallery[gid]["embedding"]
                new_emb = 0.9 * old_emb + 0.1 * embedding
                # Fix: Re-normalize the vector
                self.global_gallery[gid]["embedding"] = new_emb / np.linalg.norm(new_emb)
                self.global_gallery[gid]["last_seen"] = now
            else:
                # Create new global identity
                gid = f"global_{str(uuid.uuid4())[:8]}"
                logger.debug(f"New Global ID assigned: {gid} for {local_id}@{feed_id}")
                self.global_gallery[gid] = {
                    "embedding": embedding,
                    "first_seen": now,
                    "last_seen": now,
                    "metadata": metadata or {}
                }

            # 4. Update local map
            if feed_id not in self.local_to_global:
                self.local_to_global[feed_id] = {}
            self.local_to_global[feed_id][local_id] = gid
            
            return gid

    def _cleanup_gallery(self, now: float):
        """Removes stale identities from the gallery."""
        # Note: Called inside self._lock in register_vehicle
        expired = [gid for gid, entry in self.global_gallery.items() if now - entry["last_seen"] > self.gallery_timeout]
        for gid in expired:
            del self.global_gallery[gid]
        
        # Size limit (evict oldest)
        if len(self.global_gallery) > self.max_gallery_size:
            sorted_gids = sorted(self.global_gallery.keys(), key=lambda k: self.global_gallery[k]["last_seen"])
            for i in range(len(self.global_gallery) - self.max_gallery_size):
                del self.global_gallery[sorted_gids[i]]

    def save_state(self):
        """Could persist gallery to disk/db here."""
        with self._lock:
            # Implementation for persistence would go here
            pass
