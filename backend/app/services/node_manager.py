import asyncio
import logging
import socket
import time
import os
import psutil
from typing import Dict, List, Optional
import redis

logger = logging.getLogger("app.services.node_manager")

class NodeManager:
    """
    Manages the registration and heartbeat of the current backend node in a Redis-based cluster.
    """
    def __init__(self, config: dict):
        self.config = config
        self.redis_url = config.get("performance", {}).get("redis_url")
        self.node_id = config.get("node_id", f"node_{socket.gethostname()}_{os.getpid()}")
        self.heartbeat_interval = config.get("node_heartbeat_interval", 5.0)
        
        self.redis = None
        if self.redis_url:
            self.redis = redis.from_url(self.redis_url, decode_responses=True)
            logger.info(f"NodeManager initialized for {self.node_id} at {self.redis_url}")
        
        self._heartbeat_task = None
        self._stop_event = asyncio.Event()

    async def start(self):
        """Starts the node heartbeat loop."""
        if not self.redis:
            logger.info("Redis not configured. NodeManager will operate in single-node mode (heartbeat disabled).")
            return

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info(f"Node heartbeat started for {self.node_id}")

    async def stop(self):
        """Stops the node heartbeat loop and unregisters."""
        self._stop_event.set()
        if self._heartbeat_task:
            await self._heartbeat_task
        
        if self.redis:
            try:
                self.redis.srem("nodes:active", self.node_id)
                self.redis.delete(f"node:stats:{self.node_id}")
                logger.info(f"Node {self.node_id} unregistered.")
            except Exception as e:
                logger.error(f"Failed to unregister node: {e}")

    async def _heartbeat_loop(self):
        while not self._stop_event.is_set():
            try:
                # 1. Gather Stats
                cpu_usage = psutil.cpu_percent()
                mem_usage = psutil.virtual_memory().percent
                # We could also get active feed count from FeedManager if injected
                
                stats = {
                    "last_heartbeat": time.time(),
                    "cpu_usage": cpu_usage,
                    "mem_usage": mem_usage,
                    "status": "online",
                    "pid": os.getpid()
                }
                
                # 2. Update Redis
                pipe = self.redis.pipeline()
                pipe.sadd("nodes:active", self.node_id)
                pipe.hmset(f"node:stats:{self.node_id}", stats)
                pipe.expire(f"node:stats:{self.node_id}", int(self.heartbeat_interval * 3)) # TTL
                pipe.execute()
                
            except Exception as e:
                logger.error(f"Node heartbeat error: {e}")
            
            await asyncio.sleep(self.heartbeat_interval)

    def get_all_nodes(self) -> List[str]:
        """Returns a list of all active node IDs."""
        if not self.redis: return [self.node_id]
        return list(self.redis.smembers("nodes:active"))

    def get_node_stats(self, node_id: str) -> Optional[Dict]:
        """Returns stats for a specific node."""
        if not self.redis: return None
        return self.redis.hgetall(f"node:stats:{node_id}")

    def find_least_loaded_node(self) -> str:
        """Heuristic to find the best node for a new feed."""
        nodes = self.get_all_nodes()
        if not nodes: return self.node_id
        
        best_node = nodes[0]
        min_load = 1000.0
        
        for nid in nodes:
            stats = self.get_node_stats(nid)
            if not stats: continue
            
            # Simple load metric: CPU + Mem
            load = float(stats.get("cpu_usage", 100)) + float(stats.get("mem_usage", 100))
            if load < min_load:
                min_load = load
                best_node = nid
                
        return best_node
