import time
import numpy as np
import json
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

logger = logging.getLogger("app.services.reid")

class GlobalReIDManager:
    def __init__(self, config: dict):
        import torch
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
        
        # Redis-Atomic Global Counter
        self.global_counter = 1
        if self.redis:
            try:
                # Synchronize counter with Redis to prevent collisions across workers
                current_remote = self.redis.get("reid:global_counter")
                if current_remote:
                    self.global_counter = int(current_remote)
                else:
                    self.redis.set("reid:global_counter", self.global_counter)
            except Exception as e:
                logger.warning(f"Could not sync ReID counter with Redis: {e}")

        self.last_cleanup_time = time.time()
        self.gallery_ids: List[str] = []
        # R2 Fix: Pre-allocate gallery matrix buffer instead of using np.vstack
        self._embedding_dim = 128  # Default, updated on first embedding
        self.gallery_matrix: Optional[np.ndarray] = None
        self._gallery_write_idx = 0  # Current write position in pre-allocated buffer
        
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
        """Persists the current gallery and mappings to disk using numpy (R1: replaces unsafe pickle)."""
        try:
            os.makedirs(os.path.dirname(self.persistence_path), exist_ok=True)
            save_path = self.persistence_path.replace('.pkl', '.npz')
            
            meta_json = json.dumps(self.metadata_store)
            mappings_json = json.dumps(self.local_to_global)
            
            save_kwargs = {
                'gallery_ids': np.array(self.gallery_ids, dtype=object),
                'metadata_json': np.array([meta_json]),
                'mappings_json': np.array([mappings_json]),
                'global_counter': np.array([self.global_counter]),
            }
            if self.gallery_matrix is not None:
                save_kwargs['gallery_matrix'] = self.gallery_matrix[:self._gallery_write_idx]
            
            np.savez(save_path, **save_kwargs)
            logger.info(f"ReID state saved to {save_path} ({len(self.gallery_ids)} identities)")
        except Exception as e:
            logger.error(f"Failed to save ReID state: {e}")

    def load_state(self):
        """Loads the gallery and mappings from database, numpy, or legacy pickle."""
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
                            emb_matrix = np.vstack(loaded_embs)
                            self._embedding_dim = emb_matrix.shape[1]
                            self.gallery_matrix = np.zeros((self.max_gallery_size, self._embedding_dim), dtype=np.float32)
                            self.gallery_matrix[:len(loaded_ids)] = emb_matrix
                            self._gallery_write_idx = len(loaded_ids)
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

        npz_path = self.persistence_path.replace('.pkl', '.npz')
        if os.path.exists(npz_path):
            try:
                data = np.load(npz_path, allow_pickle=True)
                self.metadata_store = json.loads(str(data['metadata_json'][0]))
                self.local_to_global = json.loads(str(data['mappings_json'][0]))
                self.global_counter = int(data['global_counter'][0])
                self.gallery_ids = list(data['gallery_ids'])
                if 'gallery_matrix' in data and len(data['gallery_matrix']) > 0:
                    emb_matrix = data['gallery_matrix']
                    self._embedding_dim = emb_matrix.shape[1]
                    self.gallery_matrix = np.zeros((self.max_gallery_size, self._embedding_dim), dtype=np.float32)
                    count = min(len(self.gallery_ids), self.max_gallery_size)
                    self.gallery_matrix[:count] = emb_matrix[:count]
                    self._gallery_write_idx = count
                logger.info(f"ReID state loaded from numpy: {npz_path}. Total IDs: {len(self.gallery_ids)}")
                return
            except Exception as e:
                logger.error(f"Failed to load ReID state from numpy: {e}")

        if os.path.exists(self.persistence_path):
            try:
                import pickle
                with open(self.persistence_path, 'rb') as f:
                    state = pickle.load(f)
                    self.metadata_store = state.get("metadata_store", {})
                    self.local_to_global = state.get("local_to_global", {})
                    self.global_counter = state.get("global_counter", 1)
                    self.gallery_ids = state.get("gallery_ids", [])
                    old_matrix = state.get("gallery_matrix")
                    if old_matrix is not None:
                        self._embedding_dim = old_matrix.shape[1]
                        self.gallery_matrix = np.zeros((self.max_gallery_size, self._embedding_dim), dtype=np.float32)
                        count = min(len(self.gallery_ids), self.max_gallery_size)
                        self.gallery_matrix[:count] = old_matrix[:count]
                        self._gallery_write_idx = count
                self.save_state()
                logger.info(f"Migrated ReID state from pickle to numpy. Total IDs: {len(self.gallery_ids)}")
            except Exception as e:
                logger.error(f"Failed to load legacy pickle state: {e}")

    def _normalize(self, vector: np.ndarray) -> np.ndarray:
        norm = np.linalg.norm(vector)
        return vector / norm if norm > 1e-6 else vector

    def get_global_id(self, feed_id: str, local_id: str) -> Optional[str]:
        with self._lock:
            if feed_id in self.local_to_global and local_id in self.local_to_global[feed_id]:
                return self.local_to_global[feed_id][local_id]
        return None

    def _sync_gpu_matrix(self):
        if self.device.type == "cpu" or self.gallery_matrix is None or self._gallery_write_idx == 0:
            self.gallery_matrix_gpu = None
            return
        try:
            active_count = self._gallery_write_idx
            if self.gallery_matrix_gpu is None or self.gallery_matrix_gpu.shape[0] != active_count:
                self.gallery_matrix_gpu = torch.from_numpy(
                    self.gallery_matrix[:active_count].copy()
                ).to(self.device)
        except Exception as e:
            logger.error(f"Failed to sync ReID gallery to GPU: {e}")
            self.gallery_matrix_gpu = None

    def match_only(self, embedding: np.ndarray) -> Optional[str]:
        embedding = self._normalize(embedding)
        with self._lock:
            if self.gallery_matrix is not None and self._gallery_write_idx > 0:
                active_matrix = self.gallery_matrix[:self._gallery_write_idx]
                self._sync_gpu_matrix()
                if self.gallery_matrix_gpu is not None:
                    try:
                        emb_tensor = torch.from_numpy(embedding).to(self.device).unsqueeze(1)
                        scores = torch.mm(self.gallery_matrix_gpu, emb_tensor).squeeze(1)
                        best_idx = torch.argmax(scores).item()
                        best_score = scores[best_idx].item()
                        if best_score > self.similarity_threshold:
                            return self.gallery_ids[best_idx]
                        return None
                    except Exception as e:
                        logger.error(f"GPU ReID matching failed: {e}")
                scores = np.dot(active_matrix, embedding)
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
                    self.redis.hset(f"reid:meta:{gid}", "last_seen", str(now))
                    self.redis.expire(f"reid:meta:{gid}", self.ttl_seconds)
                    self.redis.expire(f"reid:emb:{gid}", self.ttl_seconds)
                    return gid
            except Exception as e:
                logger.error(f"Redis ReID early check error: {e}")

        best_match_id = None
        is_new = False
        global_id = None

        with self._lock:
            if feed_id in self.local_to_global and local_id in self.local_to_global[feed_id]:
                gid = self.local_to_global[feed_id][local_id]
                if gid in self.metadata_store:
                    self.metadata_store[gid]["last_seen"] = now
                    global_id = gid

            if not global_id:
                if self.gallery_matrix is not None and self._gallery_write_idx > 0:
                    active_matrix = self.gallery_matrix[:self._gallery_write_idx]
                    self._sync_gpu_matrix()
                    if self.gallery_matrix_gpu is not None:
                        try:
                            emb_tensor = torch.from_numpy(embedding).to(self.device).unsqueeze(1)
                            scores_tensor = torch.mm(self.gallery_matrix_gpu, emb_tensor).squeeze(1)
                            best_idx = torch.argmax(scores_tensor).item()
                            best_score = scores_tensor[best_idx].item()
                        except Exception:
                            scores = np.dot(active_matrix, embedding)
                            best_idx = np.argmax(scores)
                            best_score = scores[best_idx]
                    else:
                        scores = np.dot(active_matrix, embedding)
                        best_idx = np.argmax(scores)
                        best_score = scores[best_idx]

                    if best_score > self.similarity_threshold:
                        best_match_id = self.gallery_ids[best_idx]
                        global_id = best_match_id
                        self.metadata_store[global_id]["last_seen"] = now

                if not global_id:
                    if self.redis:
                        try:
                            new_val = self.redis.incr("reid:global_counter")
                            global_id = f"GLB_{new_val}"
                            self.global_counter = new_val
                        except Exception as e:
                            logger.error(f"Redis INCR failed, falling back to local counter: {e}")
                            global_id = f"GLB_{self.global_counter}"
                            self.global_counter += 1
                    else:
                        global_id = f"GLB_{self.global_counter}"
                        self.global_counter += 1
                    
                    is_new = True
                    emb_dim = len(embedding)
                    if self.gallery_matrix is None:
                        self._embedding_dim = emb_dim
                        self.gallery_matrix = np.zeros((self.max_gallery_size, emb_dim), dtype=np.float32)
                        self._gallery_write_idx = 0
                    
                    if self._gallery_write_idx < self.max_gallery_size:
                        self.gallery_matrix[self._gallery_write_idx] = embedding
                        self._gallery_write_idx += 1
                    else:
                        oldest_idx = 0
                        oldest_time = float('inf')
                        for idx, gid_check in enumerate(self.gallery_ids):
                            ls = self.metadata_store.get(gid_check, {}).get("last_seen", 0)
                            if ls < oldest_time:
                                oldest_time = ls
                                oldest_idx = idx
                        
                        old_gid = self.gallery_ids[oldest_idx]
                        if old_gid in self.metadata_store:
                            del self.metadata_store[old_gid]
                        
                        self.gallery_matrix[oldest_idx] = embedding
                        self.gallery_ids[oldest_idx] = global_id
                        
                        self.metadata_store[global_id] = {
                            "last_seen": now,
                            "metadata": metadata
                        }
                        if feed_id not in self.local_to_global:
                            self.local_to_global[feed_id] = {}
                        self.local_to_global[feed_id][local_id] = global_id
                    
                    if not is_new or self._gallery_write_idx <= self.max_gallery_size:
                        if global_id not in self.gallery_ids:
                            self.gallery_ids.append(global_id)
                        self.metadata_store[global_id] = {
                            "last_seen": now,
                            "metadata": metadata
                        }

                if feed_id not in self.local_to_global:
                    self.local_to_global[feed_id] = {}
                self.local_to_global[feed_id][local_id] = global_id

        if is_new:
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
        
        if self.redis:
            try:
                self.redis.hset(f"reid:map:{feed_id}", local_id, global_id)
                self.redis.expire(f"reid:map:{feed_id}", self.ttl_seconds)
            except Exception: pass

        if now - self.last_cleanup_time > 60:
            with self._lock:
                if now - self.last_cleanup_time > 60:
                    self._cleanup(now)
                    if self.redis:
                        self._sync_from_redis()
        
        return global_id

    def _listen_for_updates(self):
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
                        self.gallery_matrix = np.zeros((self.max_gallery_size, embedding.shape[0]), dtype=np.float32)
                        self.gallery_matrix[0] = embedding
                        self._gallery_write_idx = 1
                        self._embedding_dim = embedding.shape[0]
                    elif self._gallery_write_idx < self.max_gallery_size:
                        self.gallery_matrix[self._gallery_write_idx] = embedding
                        self._gallery_write_idx += 1
                    self.metadata_store[gid] = {
                        "last_seen": float(meta.get(b"last_seen", time.time())),
                        "metadata": {"class_name": meta.get(b"class_name", b"unknown").decode('utf-8')}
                    }
        except Exception as e:
            logger.error(f"Failed to sync single ReID {gid}: {e}")

    def _sync_from_redis(self):
        """Loads new identities from Redis gallery and updates pre-allocated matrix."""
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
                            self.gallery_matrix = np.zeros((self.max_gallery_size, embedding.shape[0]), dtype=np.float32)
                            self.gallery_matrix[0] = embedding
                            self._gallery_write_idx = 1
                            self._embedding_dim = embedding.shape[0]
                        elif self._gallery_write_idx < self.max_gallery_size:
                            self.gallery_matrix[self._gallery_write_idx] = embedding
                            self._gallery_write_idx += 1
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
            new_ids = [self.gallery_ids[i] for i in keep_indices]
            if new_ids and self.gallery_matrix is not None:
                kept_embeddings = self.gallery_matrix[keep_indices].copy()
                self.gallery_matrix = np.zeros((self.max_gallery_size, self._embedding_dim), dtype=np.float32)
                self.gallery_matrix[:len(new_ids)] = kept_embeddings
                self._gallery_write_idx = len(new_ids)
            else:
                self.gallery_matrix = None
                self._gallery_write_idx = 0
            self.gallery_ids = new_ids
            logger.info(f"ReID Cleanup: Removed {len(expired_gids)} vehicles. Remaining: {len(new_ids)}")
            for feed_id in list(self.local_to_global.keys()):
                self.local_to_global[feed_id] = {
                    lid: gid for lid, gid in self.local_to_global[feed_id].items() 
                    if gid not in expired_gids
                }
                if not self.local_to_global[feed_id]:
                    del self.local_to_global[feed_id]
