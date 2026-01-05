import time
import numpy as np
from typing import Dict, List, Optional, Tuple
import logging
from collections import deque

logger = logging.getLogger("app.services.reid")

class GlobalReIDManager:
    def __init__(self, config: dict):
        self.config = config
        self.reid_cfg = config.get("reid", {})
        self.similarity_threshold = self.reid_cfg.get("similarity_threshold", 0.85)
        self.max_gallery_size = self.reid_cfg.get("max_gallery_size", 1000)
        self.ttl_seconds = self.reid_cfg.get("ttl_seconds", 3600) # Keep for 1 hour
        
        # Gallery of known vehicles: {global_id: {"embedding": np.ndarray, "last_seen": timestamp, "metadata": {}}}
        self.gallery: Dict[str, Dict] = {}
        # Mapping of local IDs to global IDs: {feed_id: {local_id: global_id}}
        self.local_to_global: Dict[str, Dict[str, str]] = {}
        
        self.global_counter = 1
        self.last_cleanup_time = 0

    def get_global_id(self, feed_id: str, local_id: str) -> Optional[str]:
        """Fast lookup for an already mapped local ID."""
        if feed_id in self.local_to_global and local_id in self.local_to_global[feed_id]:
            gid = self.local_to_global[feed_id][local_id]
            if gid in self.gallery:
                return gid
        return None

    def match_or_register(self, feed_id: str, local_id: str, embedding: np.ndarray, metadata: dict) -> str:
        """
        Attempts to match a local track to an existing global identity.
        If no match is found, registers a new global ID.
        """
        now = time.time()
        
        # 1. Check if already mapped in this session
        if feed_id in self.local_to_global and local_id in self.local_to_global[feed_id]:
            gid = self.local_to_global[feed_id][local_id]
            if gid in self.gallery:
                # Update gallery entry
                self.gallery[gid]["last_seen"] = now
                # Exponentially update embedding to handle lighting changes? 
                # self.gallery[gid]["embedding"] = 0.9 * self.gallery[gid]["embedding"] + 0.1 * embedding
                return gid

        # 2. Search gallery for match
        best_match_id = None
        best_score = -1
        
        # Periodic cleanup of gallery
        if now - self.last_cleanup_time > 60:
            self._cleanup()
            self.last_cleanup_time = now

        for gid, entry in self.gallery.items():
            # Don't match against itself if it was recently seen in the same feed (handled by local tracking usually)
            # but allow if there's enough distance/time
            
            score = np.dot(embedding, entry["embedding"])
            if score > self.similarity_threshold and score > best_score:
                best_score = score
                best_match_id = gid

        if best_match_id:
            global_id = best_match_id
            logger.info(f"ReID Match: Local {local_id}@{feed_id} -> Global {global_id} (Score: {best_score:.3f})")
        else:
            # Register new global ID
            global_id = f"GLB_{self.global_counter}"
            self.global_counter += 1
            logger.info(f"ReID New: Local {local_id}@{feed_id} -> Global {global_id}")

        # Update mappings
        if feed_id not in self.local_to_global:
            self.local_to_global[feed_id] = {}
        
        self.local_to_global[feed_id][local_id] = global_id
        
        self.gallery[global_id] = {
            "embedding": embedding,
            "last_seen": now,
            "metadata": metadata
        }
        
        return global_id

    def _cleanup(self):
        """Removes old entries from the gallery."""
        now = time.time()
        expired = [gid for gid, entry in self.gallery.items() if now - entry["last_seen"] > self.ttl_seconds]
        for gid in expired:
            del self.gallery[gid]
            # Also clean local_to_global mappings? (might be hard to find all)
            
        # Hard size limit
        if len(self.gallery) > self.max_gallery_size:
            # Remove oldest
            sorted_gids = sorted(self.gallery.keys(), key=lambda gid: self.gallery[gid]["last_seen"])
            to_remove = sorted_gids[:len(self.gallery) - self.max_gallery_size]
            for gid in to_remove:
                del self.gallery[gid]
