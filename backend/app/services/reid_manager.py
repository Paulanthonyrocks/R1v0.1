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
from threading import RLock

logger = logging.getLogger("app.services.reid")

class GlobalReIDManager:
    def __init__(self, config: dict):
        self.config = config
        self.reid_cfg = config.get("reid", {})
        self.similarity_threshold = self.reid_cfg.get("similarity_threshold", 0.85)
        self.max_gallery_size = self.reid_cfg.get("max_gallery_size", 1000)
        self.ttl_seconds = self.reid_cfg.get("ttl_seconds", 3600)
        self.persistence_path = self.reid_cfg.get("persistence_path", "backend/data/reid_gallery.pkl")

        # Embedding smoothing factor (centroid update)
        self.alpha = self.reid_cfg.get("embedding_smoothing", 0.1)
        self.counter_key = "reid:global_counter"

        # Thread safety lock
        self._lock = RLock()

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
                logger.info(f"Connected to Redis for ReID at {self.redis_cfg.get('host')}:{self.redis_cfg.get('port')}")
                
                # Start Pub/Sub listener for real-time sync
                self._sub_thread = threading.Thread(target=self._listen_for_updates, daemon=True)
                self._sub_thread.start()
            except Exception as e:
                logger.error(f"Failed to connect to Redis: {e}")

        # Initialize internal state (Local fallback/cache)
        self.metadata_store: Dict[str, Dict] = {}
        self.local_to_global: Dict[str, Dict[str, str]] = {}
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
                    "gallery_ids": self.gallery_ids,
                    "gallery_matrix": self.gallery_matrix
                }
                pickle.dump(state, f)
            logger.info(f"ReID state saved to {self.persistence_path}")
        except Exception as e:
            logger.error(f"Failed to save ReID state: {e}")

    def load_state(self):
        """Loads the gallery and mappings from database or disk."""
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
                        logger.info(f"ReID state loaded from Database. Total IDs: {len(self.gallery_ids)}")
                        return
            except Exception as e:
                logger.error(f"Failed to load ReID state from DB: {e}")

        if not os.path.exists(self.persistence_path):
            return

        try:
            with open(self.persistence_path, 'rb') as f:
                state = pickle.load(f)
                self.metadata_store = state.get("metadata_store", {})
                self.local_to_global = state.get("local_to_global", {})
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

    def match_only(self, embedding: np.ndarray) -> Optional[str]:
        embedding = self._normalize(embedding)
        with self._lock:
            if self.gallery_matrix is not None and len(self.gallery_ids) > 0:
                scores = np.dot(self.gallery_matrix, embedding)
                best_idx = np.argmax(scores)
                if scores[best_idx] > self.similarity_threshold:
                    return self.gallery_ids[best_idx]
        return None

    def match_or_register(self, feed_id: str, local_id: str, embedding: np.ndarray, metadata: dict) -> str:
        embedding = self._normalize(embedding)
        now = time.time()

        if self.redis:
            try:
                mapping_key = f"reid:map:{feed_id}"
                gid = self.redis.hget(mapping_key, local_id)
                if gid:
                    gid = gid.decode('utf-8')
                    self.redis.expire(mapping_key, self.ttl_seconds)
                    return gid
            except Exception as e:
                logger.error(f"Redis ReID error: {e}")

        with self._lock:
            if feed_id in self.local_to_global and local_id in self.local_to_global[feed_id]:
                gid = self.local_to_global[feed_id][local_id]
                if gid in self.metadata_store:
                    self.metadata_store[gid]["last_seen"] = now
                    return gid

            best_match_id = None
            best_score = -1.0

            if self.gallery_matrix is not None and len(self.gallery_ids) > 0:
                scores = np.dot(self.gallery_matrix, embedding)
                best_idx = np.argmax(scores)
                best_score = scores[best_idx]

                if best_score > self.similarity_threshold:
                    best_match_id = self.gallery_ids[best_idx]
                    
                    # --- Centroid Update ---
                    old_emb = self.gallery_matrix[best_idx]
                    new_emb = (1.0 - self.alpha) * old_emb + self.alpha * embedding
                    updated_emb = self._normalize(new_emb)
                    self.gallery_matrix[best_idx] = updated_emb
                    
                    # Sync updated embedding to Redis and DB
                    if self.redis:
                        try:
                            self.redis.set(f"reid:emb:{best_match_id}", updated_emb.tobytes(), ex=self.ttl_seconds)
                            # Broadcast update to cluster
                            self.redis.publish("reid:update_identity", f"{best_match_id}|{updated_emb.tobytes().hex()}")
                        except Exception as e:
                            logger.error(f"Failed to broadcast ReID update: {e}")
                    
                    if self.db_manager:
                        self.db_manager.save_reid_identity(
                            global_id=best_match_id, 
                            embeddings=updated_emb, 
                            metadata=self.metadata_store[best_match_id]["metadata"], 
                            last_seen=now
                        )
                    
                    logger.debug(f"ReID Centroid Updated: {local_id} -> {best_match_id}")
                
                elif self.redis:
                    if time.time() - self.last_cleanup_time > 1.0:
                         self._sync_from_redis()
                         if self.gallery_matrix is not None and len(self.gallery_ids) > 0:
                             scores = np.dot(self.gallery_matrix, embedding)
                             best_idx = np.argmax(scores)
                             best_score = scores[best_idx]
                             if best_score > self.similarity_threshold:
                                 best_match_id = self.gallery_ids[best_idx]

            if best_match_id:
                global_id = best_match_id
                self.metadata_store[global_id]["last_seen"] = now
            else:
                # --- Distributed ID Generation ---
                if self.redis:
                    try:
                        new_id_num = self.redis.incr(self.counter_key)
                        global_id = f"GLB_{new_id_num}"
                    except Exception as e:
                        logger.warning(f"Redis INCR failed, falling back to UUID: {e}")
                        import uuid
                        global_id = f"GLB_{uuid.uuid4().hex[:12]}"
                else:
                    # Local fallback (Not distributed safe)
                    import uuid
                    logger.warning("Redis unavailable, using local UUID fallback for ReID identity.")
                    global_id = f"GLB_{uuid.uuid4().hex[:12]}"
                
                if self.gallery_matrix is None:
                    self.gallery_matrix = embedding.reshape(1, -1)
                else:
                    self.gallery_matrix = np.vstack([self.gallery_matrix, embedding])
                
                self.gallery_ids.append(global_id)
                self.metadata_store[global_id] = {"last_seen": now, "metadata": metadata}
                
                if self.redis:
                    try:
                        self.redis.hset(f"reid:meta:{global_id}", mapping={
                            "last_seen": str(now),
                            "class_name": metadata.get("class_name", "unknown")
                        })
                        self.redis.set(f"reid:emb:{global_id}", embedding.tobytes())
                        self.redis.rpush("reid:gallery", global_id)
                        self.redis.publish("reid:new_identity", global_id)
                    except Exception as e:
                        logger.error(f"Failed to sync new ReID to Redis: {e}")
                
                if self.db_manager:
                    self.db_manager.save_reid_identity(global_id, embedding, metadata, now)
                
                logger.info(f"ReID New: {local_id} -> {global_id}")

            if feed_id not in self.local_to_global:
                self.local_to_global[feed_id] = {}
            self.local_to_global[feed_id][local_id] = global_id
            
            if self.redis:
                try:
                    self.redis.hset(f"reid:map:{feed_id}", local_id, global_id)
                except Exception as e:
                    logger.error(f"Failed to sync map to Redis: {e}")

            if now - self.last_cleanup_time > 60:
                self._cleanup(now)
                if self.redis:
                    self._sync_from_redis()
            
            return global_id

    def _listen_for_updates(self):
        if not self.redis: return
        try:
            pubsub = self.redis.pubsub()
            pubsub.subscribe("reid:new_identity", "reid:update_identity")
            logger.info("Subscribed to reid:new_identity and reid:update_identity for distributed sync.")
            
            for message in pubsub.listen():
                if self._stop_sub: break
                if message['type'] == 'message':
                    channel = message['channel'].decode('utf-8')
                    data = message['data'].decode('utf-8')
                    if channel == "reid:new_identity":
                        self._sync_single_id_from_redis(data)
                    elif channel == "reid:update_identity":
                        gid, emb_hex = data.split('|')
                        emb_bytes = bytes.fromhex(emb_hex)
                        self._update_single_id_from_redis(gid, emb_bytes)
        except Exception as e:
            logger.error(f"ReID Pub/Sub listener error: {e}")

    def _update_single_id_from_redis(self, gid: str, emb_bytes: bytes):
        if not self.redis or not gid: return
        with self._lock:
            if gid in self.gallery_ids:
                idx = self.gallery_ids.index(gid)
                embedding = np.frombuffer(emb_bytes, dtype=np.float32)
                self.gallery_matrix[idx] = self._normalize(embedding)
                logger.debug(f"Synced Updated ReID: {gid}")

    def _sync_single_id_from_redis(self, gid: str):
        if not self.redis or not gid: return
        if gid in self.gallery_ids: return
        try:
            meta = self.redis.hgetall(f"reid:meta:{gid}")
            if not meta: return
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
        except Exception as e:
            logger.error(f"Failed to sync single ReID {gid}: {e}")

    def _sync_from_redis(self):
        if not self.redis: return
        try:
            remote_ids = self.redis.lrange("reid:gallery", 0, -1)
            remote_ids = [rid.decode('utf-8') for rid in remote_ids]
            local_id_set = set(self.gallery_ids)
            new_ids = [rid for rid in remote_ids if rid not in local_id_set]
            if not new_ids: return
            logger.info(f"Syncing {len(new_ids)} new ReID identities from Redis.")
            for gid in new_ids:
                meta = self.redis.hgetall(f"reid:meta:{gid}")
                if not meta: continue
                emb_bytes = self.redis.get(f"reid:emb:{gid}")
                if not emb_bytes: continue
                embedding = np.frombuffer(emb_bytes, dtype=np.float32)
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
        except Exception as e:
            logger.error(f"Redis sync error: {e}")

    def _cleanup(self, now: float):
        self.last_cleanup_time = now
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
        if len(keep_indices) > self.max_gallery_size:
            keep_indices.sort(key=lambda i: self.metadata_store[self.gallery_ids[i]]["last_seen"], reverse=True)
            indices_to_remove = keep_indices[self.max_gallery_size:]
            keep_indices = keep_indices[:self.max_gallery_size]
            for i in indices_to_remove:
                gid = self.gallery_ids[i]
                expired_gids.add(gid)
                del self.metadata_store[gid]
        if len(keep_indices) < len(self.gallery_ids):
            self.gallery_ids = [self.gallery_ids[i] for i in keep_indices]
            if self.gallery_ids:
                self.gallery_matrix = self.gallery_matrix[keep_indices]
            else:
                self.gallery_matrix = None
            logger.info(f"ReID Cleanup: Removed {len(expired_gids)} vehicles.")
            
            # Prune local_to_global
            for feed_id in list(self.local_to_global.keys()):
                self.local_to_global[feed_id] = {
                    lid: gid for lid, gid in self.local_to_global[feed_id].items() 
                    if gid in self.gallery_ids
                }
                if not self.local_to_global[feed_id]:
                    del self.local_to_global[feed_id]

            # Prune metadata_store (double check in case of missed deletions)
            current_gids = set(self.gallery_ids)
            self.metadata_store = {gid: meta for gid, meta in self.metadata_store.items() if gid in current_gids}
            
            logger.info(f"ReID Registry Sizes - Gallery: {len(self.gallery_ids)}, Metadata: {len(self.metadata_store)}, Mappings: {len(self.local_to_global)}")
