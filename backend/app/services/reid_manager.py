import time
import numpy as np
import pickle
import os
from typing import Dict, List, Optional
import logging
from threading import Lock
try:
    import redis
    import redis.asyncio as aredis
except ImportError:
    redis = None
    aredis = None

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
        
        # Redis Connection
        self.redis_url = config.get("performance", {}).get("redis_url")
        self.redis = None
        if self.redis_url and redis:
            try:
                self.redis = redis.from_url(self.redis_url, decode_responses=False) # Keep binary for embeddings
                logger.info(f"Connected to Redis for ReID at {self.redis_url}")
            except Exception as e:
                logger.error(f"Failed to connect to Redis: {e}")

        # Initialize internal state (Local fallback/cache)
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

        # --- Redis Distributed Mode ---
        if self.redis:
            try:
                # 1. Check Redis Mapping (local_id -> global_id)
                mapping_key = f"reid:map:{feed_id}"
                gid = self.redis.hget(mapping_key, local_id)
                if gid:
                    gid = gid.decode('utf-8')
                    # Update TTL in Redis
                    self.redis.expire(mapping_key, self.ttl_seconds)
                    return gid

                # 2. Vectorized Search in Redis
                # Since we don't have RediSearch, we'll store the 'gallery' in a Redis List 
                # and occasionally sync it locally. For now, we'll do an atomic check for NEW ids.
                pass
            except Exception as e:
                logger.error(f"Redis ReID error: {e}")

        with self._lock:
            # 1. Check local cache first (Fastest)
            if feed_id in self.local_to_global and local_id in self.local_to_global[feed_id]:
                gid = self.local_to_global[feed_id][local_id]
                if gid in self.metadata_store:
                    self.metadata_store[gid]["last_seen"] = now
                    return gid

            # 2. Vectorized Search (Local fallback)
            best_match_id = None
            best_score = -1.0

            if self.gallery_matrix is not None and len(self.gallery_ids) > 0:
                scores = np.dot(self.gallery_matrix, embedding)
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
                
                # --- Sync to Redis ---
                if self.redis:
                    try:
                        # Save embedding and metadata to Redis
                        self.redis.hset(f"reid:meta:{global_id}", mapping={
                            "last_seen": str(now),
                            "class_name": metadata.get("class_name", "unknown")
                        })
                        self.redis.set(f"reid:emb:{global_id}", embedding.tobytes())
                        # Add to global list for other nodes to discover
                        self.redis.rpush("reid:gallery", global_id)
                    except Exception as e:
                        logger.error(f"Failed to sync new ReID to Redis: {e}")
                
                logger.info(f"ReID New: {local_id} -> {global_id}")

            # 4. Update Mappings
            if feed_id not in self.local_to_global:
                self.local_to_global[feed_id] = {}
            self.local_to_global[feed_id][local_id] = global_id
            
            # --- Sync Mapping to Redis ---
            if self.redis:
                try:
                    self.redis.hset(f"reid:map:{feed_id}", local_id, global_id)
                except Exception as e:
                    logger.error(f"Failed to sync map to Redis: {e}")

            # 5. Periodic Cleanup and Redis Sync
            if now - self.last_cleanup_time > 60:
                self._cleanup(now)
                if self.redis:
                    self._sync_from_redis()
            
            return global_id

    def _sync_from_redis(self):
        """Loads new identities from Redis gallery that are not in local matrix."""
        if not self.redis: return
        
        try:
            # 1. Get all IDs from Redis gallery
            remote_ids = self.redis.lrange("reid:gallery", 0, -1)
            remote_ids = [rid.decode('utf-8') for rid in remote_ids]
            
            # 2. Find IDs we don't have locally
            local_id_set = set(self.gallery_ids)
            new_ids = [rid for rid in remote_ids if rid not in local_id_set]
            
            if not new_ids: return
            
            logger.info(f"Syncing {len(new_ids)} new ReID identities from Redis.")
            
            for gid in new_ids:
                # Load metadata
                meta = self.redis.hgetall(f"reid:meta:{gid}")
                if not meta: continue
                
                # Load embedding
                emb_bytes = self.redis.get(f"reid:emb:{gid}")
                if not emb_bytes: continue
                
                embedding = np.frombuffer(emb_bytes, dtype=np.float32)
                
                # Update local state
                with self._lock:
                    if gid not in self.gallery_ids: # Double check
                        self.gallery_ids.append(gid)
                        if self.gallery_matrix is None:
                            self.gallery_matrix = embedding.reshape(1, -1)
                        else:
                            self.gallery_matrix = np.vstack([self.gallery_matrix, embedding])
                        
                        self.metadata_store[gid] = {
                            "last_seen": float(meta.get(b"last_seen", time.time())),
                            "metadata": {"class_name": meta.get(b"class_name", b"unknown").decode('utf-8')}
                        }
        except Exception as e:
            logger.error(f"Redis sync error: {e}")

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