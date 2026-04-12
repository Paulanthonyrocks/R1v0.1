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
        h00)
        self.ttl_seconds = self.reid_cfg.get("ttl_seconds", 3600)
        self.persistence_path = self.reid_cfg.get("persistence_path", "backend/data/reid_gallery.pkl")
        
        perf_cfg = config.get("performance", {})
        self.use_gpu = perf_cfg.get("gpu_acceleration", False)
        self.device = torch.device("cuda" if self.use_gpu and torch.cuda.is_available() else "cpu")
        self.gallery_matrix_gpu = None
        
        self._lock = Lock()
        
        self.redis_cfg = config.get("redis", {})
        self.redis = None
        self._stop_sub = False
        
        if self.redis_cfg.get("enabled", False) and redis:
            try:
                self.redis = redis.Redis(host=self.redis_cfg.get("host", "localhost"), port=self.redis_cfg.get("port", 6379), db=self.redis_cfg.get("db", 0), password=self.redis_cfg.get("password"), decode_responses=False)
                self.redis.ping()
                logger.info(f"Connected to Redis for ReID at {self.redis_cfg.get('host')}:{self.redis_cfg.get('port')}")
                self._sub_thread = threading.Thread(target=self._listen_for_updates, daemon=True)
                self._sub_thread.start()
            except Exception as e:
                logger.warning(f"Failed to connect to Redis (falling back to local mode): {e}")
                self.redis = None

        self.metadata_store: Dict[str, Dict] = {}
        self.local_to_global: Dict[str, Dict[str, str]] = {}
        
        self.global_counter = 1
        if self.redis:
            try:
                current_remote = self.redis.get("reid:global_counter")
                if current_remote: self.global_counter = int(current_remote)
                else: self.redis.set("reid:global_counter", self.global_counter)
            except Exception as e:
                logger.warning(f"Could not sync ReID counter with Redis: {e}")

        self.last_cleanup_time = time.time()
        self.gallery_ids: List[str] = []
        self._embedding_dim = 128
        self.gallery_matrix: Optional[np.ndarray] = None
        self._gallery_write_idx = 0
        
        self.db_manager = None
        self.load_state()

    def set_db_manager(self, db_manager):
        self.db_manager = db_manager
        if not self.gallery_ids: self.load_state()

    def save_state(self):
        try:
            os.makedirs(os.path.dirname(self.persistence_path), exist_ok=True)
            save_path = self.persistence_path.replace('.pkl', '.npz')
            save_kwargs = {'gallery_ids': np.array(self.gallery_ids, dtype=object), 'metadata_json': np.array([json.dumps(self.metadata_store)]), 'mappings_json': np.array([json.dumps(self.local_to_global)]), 'global_counter': np.array([self.global_counter])}
            if self.gallery_matrix is not None: save_kwargs['gallery_matrix'] = self.gallery_matrix[:self._gallery_write_idx]
            np.savez(save_path, **save_kwargs)
            logger.info(f"ReID state saved to {save_path} ({len(self.gallery_ids)} identities)")
        except Exception as e:
            logger.error(f"Failed to save ReID state: {e}")

    def load_state(self):
        if self.db_manager:
            try:
                identities = self.db_manager.get_recent_reid_identities(limit=self.max_gallery_size)
                if identities:
                    loaded_ids, loaded_embs = [], []
                    for idt in identities:
                        gid, emb_bytes = idt["global_id"], idt["embeddings"]
                        if not emb_bytes: continue
                        loaded_ids.append(gid); loaded_embs.append(np.frombuffer(emb_bytes, dtype=np.float32))
                        self.metadata_store[gid] = idt["metadata"]
                        self.metadata_store[gid]["last_seen"] = idt["last_seen"]
                    
                    if loaded_ids:
                        with self._lock:
                            self.gallery_ids = loaded_ids
                            emb_matrix = np.vstack(loaded_embs)
                            self._embedding_dim, self.gallery_matrix = emb_matrix.shape[1], np.zeros((self.max_gallery_size, self._embedding_dim), dtype=np.float32)
                            self.gallery_matrix[:len(loaded_ids)], self._gallery_write_idx = emb_matrix, len(loaded_ids)
                            for gid in loaded_ids:
                                if gid.startswith("GLB_"):
                                    try: self.global_counter = max(self.global_counter, int(gid.split("_")[1]) + 1)
                                    except: pass
                        logger.info(f"ReID state loaded from Database. Total IDs: {len(self.gallery_ids)}")
                        return
            except Exception as e:
                logger.error(f"Failed to load ReID state from DB: {e}")

        npz_path = self.persistence_path.replace('.pkl', '.npz')
        if os.path.exists(npz_path):
            try:
                data = np.load(npz_path, allow_pickle=True)
                self.metadata_store, self.local_to_global, self.global_counter, self.gallery_ids = json.loads(str(data['metadata_json'][0])), json.loads(str(data['mappings_json'][0])), int(data['global_counter'][0]), list(data['gallery_ids'])
                if 'gallery_matrix' in data and len(data['gallery_matrix']) > 0:
                    emb_matrix = data['gallery_matrix']
                    self._embedding_dim, self.gallery_matrix, count = emb_matrix.shape[1], np.zeros((self.max_gallery_size, self._embedding_dim), dtype=np.float32), min(len(self.gallery_ids), self.max_gallery_size)
                    self.gallery_matrix[:count], self._gallery_write_idx = emb_matrix[:count], count
                logger.info(f"ReID state loaded from numpy: {npz_path}. Total IDs: {len(self.gallery_ids)}")
                return
            except Exception as e:
                logger.error(f"Failed to load ReID state from numpy: {e}")

    def _normalize(self, vector: np.ndarray) -> np.ndarray:
        norm = np.linalg.norm(vector)
        return vector / norm if norm > 1e-6 else vector

    def get_global_id(self, feed_id: str, local_id: str) -> Optional[str]:
        with self._lock:
            return self.local_to_global.get(feed_id, {}).get(local_id)

    def _sync_gpu_matrix(self):
        if self.device.type == "cpu" or self.gallery_matrix is None or self._gallery_write_idx == 0: self.gallery_matrix_gpu = None; return
        try:
            if self.gallery_matrix_gpu is None or self.gallery_matrix_gpu.shape[0] != self._gallery_write_idx:
                self.gallery_matrix_gpu = torch.from_numpy(self.gallery_matrix[:self._gallery_write_idx].copy()).to(self.device)
        except Exception as e:
            logger.error(f"Failed to sync ReID gallery to GPU: {e}"); self.gallery_matrix_gpu = None

    def match_only(self, embedding: np.ndarray) -> Optional[str]:
        embedding = self._normalize(embedding)
        with self._lock:
            if self.gallery_matrix is not None and self._gallery_write_idx > 0:
                self._sync_gpu_matrix()
                if self.gallery_matrix_gpu is not None:
                    try:
                        scores = torch.mm(self.gallery_matrix_gpu, torch.from_numpy(embedding).to(self.device).unsqueeze(1)).squeeze(1)
                        best_idx, best_score = torch.argmax(scores).item(), scores[best_idx].item()
                        if best_score > self.similarity_threshold: return self.gallery_ids[best_idx]
                    except Exception as e:
                        logger.error(f"GPU ReID matching failed: {e}")
                scores = np.dot(self.gallery_matrix[:self._gallery_write_idx], embedding)
                best_idx = np.argmax(scores)
                if scores[best_idx] > self.similarity_threshold: return self.gallery_ids[best_idx]
        return None

    def match_or_register(self, feed_id: str, local_id: str, embedding: np.ndarray, metadata: dict) -> str:
        embedding, now = self._normalize(embedding), time.time()
        if self.redis:
            try:
                mapping_key = f"reid:map:{feed_id}"
                if (gid := self.redis.hget(mapping_key, local_id)): gid = gid.decode('utf-8'); self.redis.expire(mapping_key, self.ttl_seconds); self.redis.hset(f"reid:meta:{gid}", "last_seen", str(now)); self.redis.expire(f"reid:meta:{gid}", self.ttl_seconds); self.redis.expire(f"reid:emb:{gid}", self.ttl_seconds); return gid
            except Exception as e:
                logger.error(f"Redis ReID early check error: {e}")

        global_id, is_new = None, False
        with self._lock:
            if (gid := self.local_to_global.get(feed_id, {}).get(local_id)) and gid in self.metadata_store: self.metadata_store[gid]["last_seen"] = now; global_id = gid

            if not global_id and self.gallery_matrix is not None and self._gallery_write_idx > 0:
                scores = np.dot(self.gallery_matrix[:self._gallery_write_idx], embedding)
                if (best_score := np.max(scores)) > self.similarity_threshold: global_id = self.gallery_ids[np.argmax(scores)]; self.metadata_store[global_id]["last_seen"] = now

            if not global_id:
                is_new = True
                if self.redis: global_id = f"GLB_{self.redis.incr('reid:global_counter')}"
                else: self.global_counter += 1; global_id = f"GLB_{self.global_counter}"

                if self.gallery_matrix is None: self._embedding_dim = len(embedding); self.gallery_matrix = np.zeros((self.max_gallery_size, self._embedding_dim), dtype=np.float32); self._gallery_write_idx = 0

                write_idx = self._gallery_write_idx
                if write_idx >= self.max_gallery_size:
                    oldest_idx = min(range(self._gallery_write_idx), key=lambda i: self.metadata_store.get(self.gallery_ids[i], {}).get("last_seen", float('inf')))
                    evicted_gid = self.gallery_ids[oldest_idx]
                    if evicted_gid in self.metadata_store:
                        del self.metadata_store[evicted_gid]
                    write_idx = oldest_idx

                self.gallery_matrix[write_idx] = embedding
                if write_idx >= self._gallery_write_idx:
                    self.gallery_ids.append(global_id)
                    self._gallery_write_idx += 1
                else:
                    self.gallery_ids[write_idx] = global_id
                
                self.metadata_store[global_id] = {"last_seen": now, "metadata": metadata}

            if feed_id not in self.local_to_global: self.local_to_global[feed_id] = {}
            self.local_to_global[feed_id][local_id] = global_id

        if is_new and self.redis: self.redis.publish("reid:new_identity", global_id)
        if now - self.last_cleanup_time > 60: self._cleanup(now)
        return global_id

    def _listen_for_updates(self):
        if not self.redis: return
        try:
            pubsub = self.redis.pubsub()
            pubsub.subscribe("reid:new_identity")
            for message in pubsub.listen():
                if self._stop_sub: break
                if message['type'] == 'message': self._sync_single_id_from_redis(message['data'].decode('utf-8'))
        except Exception as e:
            logger.error(f"ReID Pub/Sub listener error: {e}")

    def _sync_single_id_from_redis(self, gid: str):
        if not self.redis or not gid or gid in self.gallery_ids: return
        try:
            if not (meta := self.redis.hgetall(f"reid:meta:{gid}")) or not (emb_bytes := self.redis.get(f"reid:emb:{gid}")): return
            embedding = self._normalize(np.frombuffer(emb_bytes, dtype=np.float32))
            with self._lock:
                if gid not in self.gallery_ids:
                    if self.gallery_matrix is None: self._embedding_dim = len(embedding); self.gallery_matrix = np.zeros((self.max_gallery_size, self._embedding_dim), dtype=np.float32); self._gallery_write_idx = 0
                    
                    write_idx = self._gallery_write_idx
                    if write_idx >= self.max_gallery_size:
                        oldest_idx = min(range(self._gallery_write_idx), key=lambda i: self.metadata_store.get(self.gallery_ids[i], {}).get("last_seen", float('inf')))
                        del self.metadata_store[self.gallery_ids[oldest_idx]]
                        write_idx = oldest_idx
                    
                    self.gallery_matrix[write_idx] = embedding
                    if write_idx == self._gallery_write_idx: self.gallery_ids.append(gid); self._gallery_write_idx += 1
                    else: self.gallery_ids[write_idx] = gid
                    self.metadata_store[gid] = {"last_seen": float(meta.get(b"last_seen", time.time())), "metadata": {"class_name": meta.get(b"class_name", b"unknown").decode('utf-8')}}
        except Exception as e:
            logger.error(f"Failed to sync single ReID {gid}: {e}")

    def _cleanup(self, now: float):
        self.last_cleanup_time = now
        with self._lock:
            keep_indices = [i for i, gid in enumerate(self.gallery_ids) if now - self.metadata_store.get(gid, {}).get("last_seen", 0) <= self.ttl_seconds]
            if len(keep_indices) < len(self.gallery_ids):
                expired_gids = {self.gallery_ids[i] for i in range(len(self.gallery_ids)) if i not in keep_indices}
                self.gallery_ids = [self.gallery_ids[i] for i in keep_indices]
                if self.gallery_ids and self.gallery_matrix is not None:
                    self.gallery_matrix[:len(self.gallery_ids)] = self.gallery_matrix[keep_indices]
                self._gallery_write_idx = len(self.gallery_ids)
                for gid in expired_gids: del self.metadata_store[gid]
                for feed_id, mapping in self.local_to_global.items(): self.local_to_global[feed_id] = {lid: gid for lid, gid in mapping.items() if gid not in expired_gids}
