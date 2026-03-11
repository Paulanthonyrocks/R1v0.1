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
import threading
import torch

logger = logging.getLogger("app.services.reid")

class GlobalReIDManager:
    def __init__(self, config: dict):
        self.config = config
        self.reid_cfg = config.get("reid", {})
        self.similarity_threshold = self.reid_cfg.get("similarity_threshold", 0.85)
        self.max_gallery_size = self.reid_cfg.get("max_gallery_size", 1000)
        self.ttl_seconds = self.reid_cfg.get("ttl_seconds", 3600)
        self.persistence_path = self.reid_cfg.get("persistence_path", "backend/data/reid_gallery.pkl")
        
        # GPU Setup
        perf_cfg = config.get("performance", {})
        self.use_gpu = perf_cfg.get("gpu_acceleration", False)
        self.device = torch.device("cuda" if self.use_gpu and torch.cuda.is_available() else "cpu")
        self.gallery_matrix_gpu = None
        
        # Thread safety lock
        self._lock = Lock()
        
        # Redis Connection
        self.redis_cfg = config.get("redis", {})
        self.redis = None
        self._stop_sub = False
        
        if self.redis_cfg.get("enabled", False) and redis:
            try:
                self.redis = redis.Redis(
                    host=self.redis_cfg.get("host", "localhost"),
                    port=self.redis_cfg.get("port", 6379),
                    db=self.redis_cfg.get("db", 0),
                    password=self.redis_cfg.get("password"),
                    decode_responses=False
                )
                # Verify connection before claiming success
                self.redis.ping()
                logger.info(f"Connected to Redis for ReID at {self.redis_cfg.get('host')}:{self.redis_cfg.get('port')}")
                
                # Start Pub/Sub listener for real-time sync
                self._sub_thread = threading.Thread(target=self._listen_for_updates, daemon=True)
                self._sub_thread.start()
            except Exception as e:
                logger.warning(f"Failed to connect to Redis (falling back to local mode): {e}")
                self.redis = None

        # Initialize internal state (Local fallback/cache)
        self.metadata_store: Dict[str, Dict] = {}
        self.local_to_global: Dict[str, Dict[str, str]] = {}
        self.global_counter = 1
        self.last_cleanup_time = time.time()
        self.gallery_ids: List[str] = []
        self.gallery_matrix: Optional[np.ndarray] = None 
        
        # Database Manager
        self.db_manager = None

        # Load existing state if available
        self.load_state()

    def set_db_manager(self, db_manager):
        """Sets the database manager for persistent storage."""
        self.db_manager = db_manager
        # Re-load from DB if local state is empty
        if not self.gallery_ids:
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
        """Loads the gallery and mappings from database or disk."""
        # 1. Try Database first (Preferred)
        if self.db_manager:
            try:
                identities = self.db_manager.get_recent_reid_identities(limit=self.max_gallery_size)
                if identities:
                    loaded_ids = []
                    loaded_embs = []
                    for idt in identities:
                        gid = idt["global_id"]
                        emb_bytes = idt["embeddings"]
                        if not emb_bytes: continue
                        
                        embedding = np.frombuffer(emb_bytes, dtype=np.float32)
                        loaded_ids.append(gid)
                        loaded_embs.append(embedding)
                        self.metadata_store[gid] = idt["metadata"]
                        self.metadata_store[gid]["last_seen"] = idt["last_seen"]
                    
                    if loaded_ids:
                        with self._lock:
                            self.gallery_ids = loaded_ids
                            self.gallery_matrix = np.vstack(loaded_embs)
                            # Update global_counter to avoid ID collisions
                            for gid in loaded_ids:
                                if gid.startswith("GLB_"):
                                    try:
                                        num = int(gid.split("_")[1])
                                        self.global_counter = max(self.global_counter, num + 1)
                                    except: pass
                        logger.info(f"ReID state loaded from Database. Total IDs: {len(self.gallery_ids)}")
                        return
            except Exception as e:
                logger.error(f"Failed to load ReID state from DB: {e}")

        # 2. Fallback to Local Pickle
        if not os.path.exists(self.persistence_path):
            logger.info("No ReID persistence fallback found. Starting fresh.")
            return

        try:
            with open(self.persistence_path, 'rb') as f:
                state = pickle.load(f)
                self.metadata_store = state.get("metadata_store", {})
                self.local_to_global = state.get("local_to_global", {})
                self.global_counter = state.get("global_counter", 1)
                self.gallery_ids = state.get("gallery_ids", [])
                self.gallery_matrix = state.get("gallery_matrix")
            logger.info(f"ReID state loaded from Pickle: {self.persistence_path}. Total IDs: {len(self.gallery_ids)}")
        except Exception as e:
            logger.error(f"Failed to load ReID state from pickle: {e}")

    def _normalize(self, vector: np.ndarray) -> np.ndarray:
        norm = np.linalg.norm(vector)
        return vector / norm if norm > 1e-6 else vector

    def get_global_id(self, feed_id: str, local_id: str) -> Optional[str]:
        with self._lock:
            if feed_id in self.local_to_global and local_id in self.local_to_global[feed_id]:
                return self.local_to_global[feed_id][local_id]
        return None

    def _sync_gpu_matrix(self):
        """Moves the local gallery matrix to GPU for high-speed matching."""
        if self.device.type == "cpu" or self.gallery_matrix is None:
            self.gallery_matrix_gpu = None
            return

        try:
            # Only sync if sizes changed or if we don't have a GPU matrix yet
            if self.gallery_matrix_gpu is None or self.gallery_matrix_gpu.shape[0] != self.gallery_matrix.shape[0]:
                self.gallery_matrix_gpu = torch.from_numpy(self.gallery_matrix).to(self.device)
        except Exception as e:
            logger.error(f"Failed to sync ReID gallery to GPU: {e}")
            self.gallery_matrix_gpu = None

    def match_only(self, embedding: np.ndarray) -> Optional[str]:
        """Attempts to match an embedding against the gallery without registering a new one."""
        embedding = self._normalize(embedding)
        with self._lock:
            if self.gallery_matrix is not None and len(self.gallery_ids) > 0:
                # Try GPU Matching First
                self._sync_gpu_matrix()
                if self.gallery_matrix_gpu is not None:
                    try:
                        emb_tensor = torch.from_numpy(embedding).to(self.device).unsqueeze(1)
                        # Perform dot product (Matrix Multiplication) on GPU
                        scores = torch.mm(self.gallery_matrix_gpu, emb_tensor).squeeze(1)
                        best_idx = torch.argmax(scores).item()
                        best_score = scores[best_idx].item()
                        if best_score > self.similarity_threshold:
                            return self.gallery_ids[best_idx]
                        return None
                    except Exception as e:
                        logger.error(f"GPU ReID matching failed: {e}")
                        # Fallback to CPU

                # CPU Fallback
                scores = np.dot(self.gallery_matrix, embedding)
                best_idx = np.argmax(scores)
                if scores[best_idx] > self.similarity_threshold:
                    return self.gallery_ids[best_idx]
        return None

    def match_or_register(self, feed_id: str, local_id: str, embedding: np.ndarray, metadata: dict) -> str:
        # Normalize immediately
        embedding = self._normalize(embedding)
        now = time.time()

        # 1. Check Redis Mapping (local_id -> global_id) - OUTSIDE LOCK
        if self.redis:
            try:
                mapping_key = f"reid:map:{feed_id}"
                gid = self.redis.hget(mapping_key, local_id)
                if gid:
                    gid = gid.decode('utf-8')
                    # Update TTL and last_seen in Redis (Fire and forget-ish)
                    self.redis.expire(mapping_key, self.ttl_seconds)
                    self.redis.hset(f"reid:meta:{gid}", "last_seen", str(now))
                    self.redis.expire(f"reid:meta:{gid}", self.ttl_seconds)
                    self.redis.expire(f"reid:emb:{gid}", self.ttl_seconds)
                    return gid
            except Exception as e:
                logger.error(f"Redis ReID early check error: {e}")

        best_match_id = None
        is_new = False
        global_id = None

        # 2. Local Matching - INSIDE LOCK
        with self._lock:
            # 2a. Check local cache again (for race conditions between threads)
            if feed_id in self.local_to_global and local_id in self.local_to_global[feed_id]:
                gid = self.local_to_global[feed_id][local_id]
                if gid in self.metadata_store:
                    self.metadata_store[gid]["last_seen"] = now
                    global_id = gid

            if not global_id:
                # 2b. Vectorized Search (Local fallback)
                if self.gallery_matrix is not None and len(self.gallery_ids) > 0:
                    # GPU/CPU Matching
                    self._sync_gpu_matrix()
                    if self.gallery_matrix_gpu is not None:
                        try:
                            emb_tensor = torch.from_numpy(embedding).to(self.device).unsqueeze(1)
                            scores_tensor = torch.mm(self.gallery_matrix_gpu, emb_tensor).squeeze(1)
                            best_idx = torch.argmax(scores_tensor).item()
                            best_score = scores_tensor[best_idx].item()
                        except Exception:
                            scores = np.dot(self.gallery_matrix, embedding)
                            best_idx = np.argmax(scores)
                            best_score = scores[best_idx]
                    else:
                        scores = np.dot(self.gallery_matrix, embedding)
                        best_idx = np.argmax(scores)
                        best_score = scores[best_idx]

                    if best_score > self.similarity_threshold:
                        best_match_id = self.gallery_ids[best_idx]
                        global_id = best_match_id
                        self.metadata_store[global_id]["last_seen"] = now

                # 2c. Register New Locally
                if not global_id:
                    global_id = f"GLB_{self.global_counter}"
                    self.global_counter += 1
                    is_new = True
                    
                    if self.gallery_matrix is None:
                        self.gallery_matrix = embedding.reshape(1, -1)
                    else:
                        self.gallery_matrix = np.vstack([self.gallery_matrix, embedding])
                    
                    self.gallery_ids.append(global_id)
                    self.metadata_store[global_id] = {
                        "last_seen": now,
                        "metadata": metadata
                    }

                # 2d. Update local mapping
                if feed_id not in self.local_to_global:
                    self.local_to_global[feed_id] = {}
                self.local_to_global[feed_id][local_id] = global_id

        # 3. Persistent Sync - OUTSIDE LOCK
        if is_new:
            # New Identity Sync
            if self.redis:
                try:
                    self.redis.hset(f"reid:meta:{global_id}", mapping={
                        "last_seen": str(now),
                        "class_name": metadata.get("class_name", "unknown")
                    })
                    self.redis.set(f"reid:emb:{global_id}", embedding.tobytes())
                    self.redis.expire(f"reid:meta:{global_id}", self.ttl_seconds)
                    self.redis.expire(f"reid:emb:{global_id}", self.ttl_seconds)
                    self.redis.rpush("reid:gallery", global_id)
                    self.redis.publish("reid:new_identity", global_id)
                except Exception as e:
                    logger.error(f"Failed to sync new ReID to Redis: {e}")
            
            if self.db_manager:
                try:
                    self.db_manager.save_reid_identity(
                        global_id=global_id,
                        embeddings=embedding,
                        metadata=metadata,
                        last_seen=now
                    )
                except Exception as e:
                    logger.error(f"Failed to save ReID to DB: {e}")
        
        # Mapping Sync
        if self.redis:
            try:
                self.redis.hset(f"reid:map:{feed_id}", local_id, global_id)
                self.redis.expire(f"reid:map:{feed_id}", self.ttl_seconds)
            except Exception: pass

        # 4. Periodic Cleanup (Throttled)
        if now - self.last_cleanup_time > 60:
            # Cleanup still needs lock but it is infrequent
            with self._lock:
                if now - self.last_cleanup_time > 60:
                    self._cleanup(now)
                    if self.redis:
                        # Sync from redis could be slow, but it is infrequent
                        # TODO: Consider making this async or a separate task
                        self._sync_from_redis()
        
        return global_id

    def _listen_for_updates(self):
        """Background thread listening for new ReID identities via Pub/Sub."""
        if not self.redis: return
        try:
            pubsub = self.redis.pubsub()
            pubsub.subscribe("reid:new_identity")
            logger.info("Subscribed to reid:new_identity for distributed sync.")
            
            for message in pubsub.listen():
                if self._stop_sub: break
                if message['type'] == 'message':
                    gid = message['data'].decode('utf-8')
                    self._sync_single_id_from_redis(gid)
        except Exception as e:
            logger.error(f"ReID Pub/Sub listener error: {e}")

    def _sync_single_id_from_redis(self, gid: str):
        """Fetches a specific identity from Redis and adds it to local gallery."""
        if not self.redis or not gid: return
        if gid in self.gallery_ids: return
        
        try:
            # Load metadata
            meta = self.redis.hgetall(f"reid:meta:{gid}")
            if not meta: return
            
            # Load embedding
            emb_bytes = self.redis.get(f"reid:emb:{gid}")
            if not emb_bytes: return
            
            embedding = np.frombuffer(emb_bytes, dtype=np.float32)
            embedding = self._normalize(embedding)
            
            with self._lock:
                if gid not in self.gallery_ids:
                    self.gallery_ids.append(gid)
                    if self.gallery_matrix is None:
                        self.gallery_matrix = embedding.reshape(1, -1)
                    else:
                        self.gallery_matrix = np.vstack([self.gallery_matrix, embedding])
                    
                    self.metadata_store[gid] = {
                        "last_seen": float(meta.get(b"last_seen", time.time())),
                        "metadata": {"class_name": meta.get(b"class_name", b"unknown").decode('utf-8')}
                    }
            logger.debug(f"Synced Recieved ReID Sync: {gid}")
        except Exception as e:
            logger.error(f"Failed to sync single ReID {gid}: {e}")

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

            # NOTE: We do NOT explicitly delete from Redis in cleanup anymore.
            # We rely on Redis TTL (set via expire calls) to clean up old keys globally.
            # This avoids race conditions where one node deletes a key that another node is still using.
            
            # CLEANUP MAPPINGS (Fixes the memory leak)
            for feed_id in list(self.local_to_global.keys()):
                # Filter dictionary inplace
                self.local_to_global[feed_id] = {
                    lid: gid for lid, gid in self.local_to_global[feed_id].items() 
                    if gid not in expired_gids
                }
                if not self.local_to_global[feed_id]:
                    del self.local_to_global[feed_id]