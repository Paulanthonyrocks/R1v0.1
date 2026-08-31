import time
import numpy as np
import pickle
import os
import threading
from typing import Dict, List, Optional, Tuple
import logging
from threading import RLock

logger = logging.getLogger("app.services.reid")

class GlobalReIDManager:
    def __init__(self, config: dict):
        self.config = config
        # reid config lives under vehicle_detection.reid in config.yaml -- SAME
        # key as ml/reid_manager.py & ml/reid_model.py (both read
        # vehicle_detection.reid). Reading the top-level `reid` key (absent in
        # the shipped config) silently fell back to every default: the operator's
        # similarity_threshold: 0.80 never applied and the effective threshold
        # was the 0.85 default -- stricter, so a re-created track after a
        # detection gap more often FAILED to match the gallery and was
        # re-registered as a NEW global id (the "re-recording old vehicles as
        # new" symptom). Honor the nested key like every sibling class does.
        self.reid_cfg = config.get("vehicle_detection", {}).get("reid", {})
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
        
        try:
            import redis
            if self.redis_cfg.get("enabled", False):
                # Use connection pool for stability
                pool = redis.ConnectionPool(
                    host=self.redis_cfg.get("host", "localhost"),
                    port=self.redis_cfg.get("port", 6379),
                    db=self.redis_cfg.get("db", 0),
                    password=self.redis_cfg.get("password"),
                    decode_responses=False,
                    max_connections=self.redis_cfg.get("max_connections", 10),
                    retry_on_timeout=self.redis_cfg.get("retry_on_timeout", True),
                    socket_keepalive=self.redis_cfg.get("socket_keepalive", True),
                    socket_connect_timeout=self.redis_cfg.get("socket_connect_timeout", 5),
                    socket_timeout=self.redis_cfg.get("socket_timeout", 5),
                    health_check_interval=self.redis_cfg.get("health_check_interval", 30),
                )
                self.redis = redis.Redis(connection_pool=pool)
                logger.info(f"Connected to Redis for ReID at {self.redis_cfg.get('host')}:{self.redis_cfg.get('port')} (pool: max_connections={self.redis_cfg.get('max_connections', 10)})")
                
                # Start Pub/Sub listener for real-time sync
                self._sub_thread = threading.Thread(target=self._listen_for_updates, daemon=True)
                self._sub_thread.start()
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")

        # Initialize internal state
        self.metadata_store: Dict[str, Dict] = {}
        self.local_to_global: Dict[str, Dict[str, str]] = {}
        self.last_cleanup_time = time.time()
        self.last_sync_time = time.time()
        # Throttle the FULL Redis re-sync (see _sync_from_redis). match_or_register
        # requests a full sync whenever the in-memory gallery misses a vector AND
        # the last full sync was >10s ago; without this floor, every cache miss on
        # a cold/partially-warmed instance re-pulls the ENTIRE remote gallery
        # (~1500-2000 ids each time) and vstacks it in again -- the observed
        # "Syncing N new identities" storm (55 bursts in ~4 min) that floods the log
        # and churns Redis with ~100k round-trips/run. Steady-state upkeep is owned
        # by the pub/sub listener (_sync_single_id_from_redis); the full pull is a
        # cold-start / catch-up path only and must not run more often than this.
        self.full_sync_interval = self.reid_cfg.get("full_sync_interval_seconds", 30.0)
        self.last_full_sync_time = 0.0  # force one full pull on first miss
        self.gallery_ids: List[str] = []
        self.gallery_matrix: Optional[np.ndarray] = None 
        
        self.db_manager = None
        self.load_state()

    def set_db_manager(self, db_manager):
        self.db_manager = db_manager
        if not self.gallery_ids:
            self.load_state()

    def shutdown(self):
        """Gracefully shuts down the ReID manager and persists state."""
        self._stop_sub = True
        # Audit M4: join the listener so "shutdown" is actually shutdown. The
        # thread spends up to 5s in its retry sleep, so bound the wait there;
        # it is a daemon anyway, so a timeout is safe.
        thread = getattr(self, "_sub_thread", None)
        if thread and thread.is_alive():
            thread.join(timeout=6.0)
        self.save_state()
        logger.info("GlobalReIDManager shutdown complete.")

    def save_state(self):
        try:
            os.makedirs(os.path.dirname(self.persistence_path), exist_ok=True)
            with self._lock:
                state = {
                    "metadata_store": self.metadata_store,
                    "local_to_global": self.local_to_global,
                    "gallery_ids": self.gallery_ids,
                    "gallery_matrix": self.gallery_matrix
                }
            with open(self.persistence_path, 'wb') as f:
                pickle.dump(state, f)
            logger.info(f"ReID state saved to {self.persistence_path}")
        except Exception as e:
            logger.error(f"Failed to save ReID state: {e}")

    def load_state(self):
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
                        
                        embedding = self._normalize(np.frombuffer(emb_bytes, dtype=np.float32))
                        loaded_ids.append(gid)
                        loaded_embs.append(embedding)
                        
                        with self._lock:
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
                with self._lock:
                    self.metadata_store = state.get("metadata_store", {})
                    self.local_to_global = state.get("local_to_global", {})
                    self.gallery_ids = state.get("gallery_ids", [])
                    self.gallery_matrix = state.get("gallery_matrix")
            logger.info(f"ReID state loaded from Pickle. Total IDs: {len(self.gallery_ids)}")
        except Exception as e:
            logger.error(f"Failed to load ReID state from pickle: {e}")

    def _normalize(self, vector: np.ndarray) -> np.ndarray:
        norm = np.linalg.norm(vector)
        return vector / norm if norm > 1e-6 else vector

    def get_global_id(self, feed_id: str, local_id: str) -> Optional[str]:
        with self._lock:
            return self.local_to_global.get(feed_id, {}).get(local_id)

    def distinct_vehicle_count(self) -> int:
        """Number of distinct global vehicle identities currently tracked.

        This is the authoritative system-wide unique-vehicle count: a vehicle
        seen in multiple feeds shares a single global_id here, so the size is
        deduplicated across feeds (used by the KPI `total_flow`, audit #2).
        Bounded by TTL + max_gallery_size, so it reflects distinct vehicles
        within the retention window rather than all-time.
        """
        with self._lock:
            return len(self.gallery_ids)

    def match_only(self, embedding: np.ndarray) -> Optional[str]:
        embedding = self._normalize(embedding)
        with self._lock:
            if self.gallery_matrix is not None and len(self.gallery_ids) > 0:
                scores = np.dot(self.gallery_matrix, embedding)
                best_idx = np.argmax(scores)
                if scores[best_idx] > self.similarity_threshold:
                    return self.gallery_ids[best_idx]
        return None

    def match_or_register(self, feed_id: str, local_id: str, embedding: np.ndarray, metadata: dict, confidence: Optional[float] = None) -> str:
        embedding = self._normalize(embedding)
        now = time.time()

        # 0. In-memory local->global check FIRST (zero network).
        # This process already mapped this local_id this session, so we must
        # return from memory before any Redis round-trip. The previous ordering
        # did a Redis hget on EVERY call -- even for known tracks -- which, under
        # traffic load (many vehicles re-detected each frame), became a synchronous
        # Redis storm that pinned each inference worker to ~1-2 fps/feed and capped
        # the whole pipeline's detectable-frame rate well below ingestion. Known
        # tracks now resolve in-process; only genuinely new local_ids fall through
        # to Redis (step 1) for cross-worker sharing.
        with self._lock:
            if feed_id in self.local_to_global and local_id in self.local_to_global[feed_id]:
                global_id = self.local_to_global[feed_id][local_id]
                if global_id in self.metadata_store:
                    self.metadata_store[global_id]["last_seen"] = now
                    if self.gallery_matrix is not None and global_id in self.gallery_ids:
                        idx = self.gallery_ids.index(global_id)
                        self.gallery_matrix[idx] = self._normalize(
                            (1.0 - self.alpha) * self.gallery_matrix[idx] + self.alpha * embedding
                        )
                return global_id

        # 1. Redis Cache Check (fallback for tracks mapped in another worker)
        if self.redis:
            try:
                mapping_key = f"reid:map:{feed_id}"
                gid_bytes = self.redis.hget(mapping_key, local_id)
                if gid_bytes:
                    gid = gid_bytes.decode('utf-8')
                    self.redis.expire(mapping_key, self.ttl_seconds)
                    
                    # CRITICAL FIX: Update local state and centroid on cache hit
                    with self._lock:
                        if gid in self.metadata_store:
                            self.metadata_store[gid]["last_seen"] = now
                            if self.gallery_matrix is not None and gid in self.gallery_ids:
                                idx = self.gallery_ids.index(gid)
                                self.gallery_matrix[idx] = self._normalize(
                                    (1.0 - self.alpha) * self.gallery_matrix[idx] + self.alpha * embedding
                                )
                        # Mirror into in-memory map so subsequent frames skip Redis
                        if feed_id not in self.local_to_global:
                            self.local_to_global[feed_id] = {}
                        self.local_to_global[feed_id][local_id] = gid
                    return gid
            except Exception as e:
                logger.error(f"Redis ReID cache error: {e}")

        # 2. Local Matching Logic
        global_id = None
        sync_emb = None
        new_reg = False
        sync_needed = False
        
        with self._lock:
            # Check existing local mapping
            if feed_id in self.local_to_global and local_id in self.local_to_global[feed_id]:
                global_id = self.local_to_global[feed_id][local_id]
                if global_id in self.metadata_store:
                    self.metadata_store[global_id]["last_seen"] = now

            # Fallback to vector search
            if not global_id and self.gallery_matrix is not None and len(self.gallery_ids) > 0:
                scores = np.dot(self.gallery_matrix, embedding)
                best_idx = np.argmax(scores)
                if scores[best_idx] > self.similarity_threshold:
                    global_id = self.gallery_ids[best_idx]
                    self.metadata_store[global_id]["last_seen"] = now
                    # Update centroid
                    updated_emb = self._normalize((1.0 - self.alpha) * self.gallery_matrix[best_idx] + self.alpha * embedding)
                    self.gallery_matrix[best_idx] = updated_emb
                    sync_emb = updated_emb
                elif self.redis and (now - self.last_sync_time > 10.0):
                    sync_needed = True

            # Registration
            if not global_id:
                redis_available = False
                if self.redis:
                    try:
                        new_id_num = self.redis.incr(self.counter_key)
                        global_id = f"GLB_{new_id_num}"
                        redis_available = True
                    except Exception as e:
                        logger.warning(f"Redis INCR failed: {e}")
                
                if not global_id:
                    import uuid
                    global_id = f"GLB_{uuid.uuid4().hex[:12]}"
                
                # Update Local State
                if self.gallery_matrix is None:
                    self.gallery_matrix = embedding.reshape(1, -1)
                else:
                    self.gallery_matrix = np.vstack([self.gallery_matrix, embedding])
                
                self.gallery_ids.append(global_id)
                self.metadata_store[global_id] = {"last_seen": now, "metadata": metadata, "confidence": confidence}
                sync_emb = embedding
                new_reg = True
                self._last_redis_status = redis_available

            # Update mappings
            if feed_id not in self.local_to_global:
                self.local_to_global[feed_id] = {}
            self.local_to_global[feed_id][local_id] = global_id

        # 3. Network Sync (Outside Lock)
        # A cache miss on a vector search requests a full re-sync, but we throttle
        # the FULL pull to full_sync_interval_seconds: the pub/sub listener already
        # keeps each instance's gallery warm incrementally, so re-pulling the
        # entire remote gallery on every miss is what caused the sync storm. We
        # still always honor a genuine miss via the incremental path -- only the
        # expensive bulk re-pull is rate-limited. The recursion is preserved so a
        # just-synced gallery gets a fresh match attempt.
        if sync_needed:
            self.last_sync_time = now
            if now - self.last_full_sync_time >= self.full_sync_interval:
                self.last_full_sync_time = now
                self._sync_from_redis()
            return self.match_or_register(feed_id, local_id, embedding, metadata, confidence=confidence)

        if sync_emb is not None:
            if self.redis:
                try:
                    # If Redis was unavailable during ID generation, skip the push
                    if not new_reg or getattr(self, '_last_redis_status', True):
                        emb_bytes = sync_emb.tobytes()
                        self.redis.set(f"reid:emb:{global_id}", emb_bytes, ex=self.ttl_seconds)
                        if not new_reg:
                            self.redis.publish("reid:update_identity", f"{global_id}|{emb_bytes.hex()}")
                        else:
                            self.redis.hset(f"reid:meta:{global_id}", mapping={
                                "last_seen": str(now),
                                "class_name": metadata.get("class_name", "unknown")
                            })
                            self.redis.rpush("reid:gallery", global_id)
                            self.redis.publish("reid:new_identity", global_id)
                except Exception as e:
                    logger.error(f"Redis sync error: {e}")

            if self.db_manager:
                self.db_manager.save_reid_identity(
                    global_id=global_id, 
                    embeddings=sync_emb, 
                    metadata=self.metadata_store.get(global_id, {}).get("metadata", metadata), 
                    last_seen=now
                )

        if self.redis:
            try:
                self.redis.hset(f"reid:map:{feed_id}", local_id, global_id)
            except Exception as e:
                logger.error(f"Failed to sync map to Redis: {e}")

        if now - self.last_cleanup_time > 60:
            self._cleanup(now)
        
        return global_id

    def _listen_for_updates(self):
        if not self.redis: return
        while not self._stop_sub:
            pubsub = None
            try:
                # Audit M4 (2026-08-23): the pubsub object is created OUTSIDE the
                # retry loop's scope and closed in `finally`. Previously a fresh
                # pubsub() was created per reconnect attempt without closing the
                # old one — every Redis disconnect leaked a socket + subscription.
                pubsub = self.redis.pubsub()
                pubsub.subscribe("reid:new_identity", "reid:update_identity")
                logger.info("Subscribed to ReID sync channels.")

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
                if not self._stop_sub:
                    logger.error(f"ReID Pub/Sub disconnected: {e}. Retrying in 5s...")
                    time.sleep(5)
                else:
                    break
            finally:
                # Always release the socket, whether we exited by stop-flag,
                # exception, or clean listen() termination.
                try:
                    if pubsub is not None:
                        pubsub.close()
                except Exception:
                    pass

    def _update_single_id_from_redis(self, gid: str, emb_bytes: bytes):
        if not self.redis or not gid: return
        embedding = self._normalize(np.frombuffer(emb_bytes, dtype=np.float32))
        with self._lock:
            if gid in self.gallery_ids:
                idx = self.gallery_ids.index(gid)
                self.gallery_matrix[idx] = embedding
                logger.debug(f"Synced Updated ReID: {gid}")

    def _sync_single_id_from_redis(self, gid: str):
        if not self.redis or not gid: return
        if gid in self.gallery_ids: return
        try:
            meta = self.redis.hgetall(f"reid:meta:{gid}")
            if not meta: return
            emb_bytes = self.redis.get(f"reid:emb:{gid}")
            if not emb_bytes: return
            embedding = self._normalize(np.frombuffer(emb_bytes, dtype=np.float32))
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
            
            with self._lock:
                local_id_set = set(self.gallery_ids)
            
            new_ids = [rid for rid in remote_ids if rid not in local_id_set]
            # Cap the pull to the local gallery's remaining capacity. The Redis
            # index is trimmed to max_gallery_size on cleanup, but the local
            # gallery is trimmed to the SAME bound on its own TTL schedule, so
            # without this cap a full-sized remote list perpetually shows
            # ~max_gallery_size "new" ids and every full sync re-pulls them
            # (the 2001-identity bursts observed in backend_services.log).
            capacity = self.max_gallery_size - len(local_id_set)
            if capacity <= 0:
                return
            new_ids = new_ids[:capacity]
            if not new_ids: return
            
            # Only log when we actually pulled something new. Under the throttled
            # full-sync this is now rare (the pub/sub path does steady-state
            # upkeep), so the log line is informative rather than spam.
            if new_ids:
                logger.info(f"Syncing {len(new_ids)} new identities from Redis.")
            for gid in new_ids:
                meta = self.redis.hgetall(f"reid:meta:{gid}")
                if not meta: continue
                emb_bytes = self.redis.get(f"reid:emb:{gid}")
                if not emb_bytes: continue
                embedding = self._normalize(np.frombuffer(emb_bytes, dtype=np.float32))
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
        with self._lock:
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
                    if gid in self.metadata_store:
                        del self.metadata_store[gid]
            if len(keep_indices) < len(self.gallery_ids):
                self.gallery_ids = [self.gallery_ids[i] for i in keep_indices]
                if self.gallery_ids:
                    self.gallery_matrix = self.gallery_matrix[keep_indices]
                else:
                    self.gallery_matrix = None
                
                # Prune using O(1) set lookups
                current_gids = set(self.gallery_ids)
                for feed_id in list(self.local_to_global.keys()):
                    self.local_to_global[feed_id] = {
                        lid: gid for lid, gid in self.local_to_global[feed_id].items() 
                        if gid in current_gids
                    }
                    if not self.local_to_global[feed_id]:
                        del self.local_to_global[feed_id]

                self.metadata_store = {gid: meta for gid, meta in self.metadata_store.items() if gid in current_gids}
                logger.info(f"ReID Cleanup: Removed {len(expired_gids)} vehicles.")

        if self.redis:
            try:
                # Keep the Redis index aligned with the LOCAL gallery bound.
                # It was max_gallery_size*2, which guaranteed the remote list
                # always exceeded what any instance can hold, so every full
                # sync saw ~1000+ permanent "new" ids (sync-storm root cause).
                self.redis.ltrim("reid:gallery", 0, self.max_gallery_size - 1)
            except Exception as e:
                logger.error(f"Failed to trim Redis gallery: {e}")

        if not self.db_manager:
            self.save_state()
