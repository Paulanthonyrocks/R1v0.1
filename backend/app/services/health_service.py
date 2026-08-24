import psutil
import logging
import asyncio
import time
from typing import Dict, Any, List
from datetime import datetime, timezone
from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum

logger = logging.getLogger("app.services.health")

class SystemHealthService:
    def __init__(self, config: Dict[str, Any], feed_manager, connection_manager):
        self.config = config
        self.fm = feed_manager
        self.cm = connection_manager
        self.enabled = config.get("health_monitoring", {}).get("enabled", True)
        self.interval = config.get("health_monitoring", {}).get("interval_seconds", 10)
        self._task: Optional[asyncio.Task] = None

    def start(self):
        if self.enabled and self._task is None:
            self._task = asyncio.create_task(self._monitoring_loop())
            logger.info("SystemHealthService started.")

    async def _monitoring_loop(self):
        while True:
            try:
                status = await self.get_full_status()
                
                # Broadcast system health to admin/dashboard topic
                message = WebSocketMessage(
                    type=WebSocketMessageTypeEnum.GENERAL_NOTIFICATION,
                    data={
                        "message_type": "system_health",
                        "status": status
                    }
                )
                await self.cm.broadcast_to_topic(message.model_dump_json(), topic="system_health")
                
            except Exception as e:
                logger.error(f"Error in health monitoring loop: {e}")
            
            await asyncio.sleep(self.interval)

    async def get_full_status(self) -> Dict[str, Any]:
        """Collects metrics from various system components."""
        cpu_usage = psutil.cpu_percent(interval=None)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('/')
        
        # GPU stats if available (optional)
        gpu_stats = []
        try:
            # Simple check if nvidia-smi is available
            import subprocess
            # Use a non-blocking way if possible, or skip for now
            pass 
        except:
            pass

        # Feed Manager stats
        feeds_info = []
        active_feeds = 0
        async with self.fm._lock:
            for fid, entry in self.fm.process_registry.items():
                is_running = entry["status"].value == "running"
                if is_running: active_feeds += 1
                feeds_info.append({
                    "feed_id": fid,
                    "status": entry["status"].value,
                    "uptime": time.time() - entry.get("start_time", time.time()) if entry.get("start_time") else 0,
                    "fps": entry.get("timer").get_fps("loop_total") if entry.get("timer") else 0
                })

        # Redis status check. AUDIT FIX (2026-08-24): get_redis_client() can block
        # for seconds on first connect (sync retries + socket timeouts) and ping()
        # is sync I/O — all on the event loop, every 10s broadcast. Offload to a
        # thread so a slow Redis never stalls the whole backend.
        redis_status = "disabled"
        try:
            from app.utils.redis_client import get_redis_client
            def _redis_probe():
                client = get_redis_client()
                return bool(client.ping())
            if await asyncio.to_thread(_redis_probe):
                redis_status = "connected"
        except Exception as e:
            redis_status = f"error: {str(e)}"

        # MongoDB status check (same blocking-I/O treatment)
        mongo_status = "disabled"
        try:
            from app.database import get_database_manager
            db_manager = get_database_manager()
            if db_manager.mongo_client:
                def _mongo_probe():
                    db_manager.mongo_client.admin.command("ismaster")
                    return True
                if await asyncio.to_thread(_mongo_probe):
                    mongo_status = "connected"
            else:
                mongo_status = "not initialized"
        except Exception as e:
            mongo_status = f"error: {str(e)}"

        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "system": {
                "cpu_percent": cpu_usage,
                "memory_percent": memory.percent,
                "memory_used_gb": memory.used / (1024**3),
                "disk_percent": disk.percent,
                "redis": redis_status,
                "mongodb": mongo_status
            },
            "application": {
                "active_feeds": active_feeds,
                "total_feeds": len(feeds_info),
                "feeds": feeds_info,
                "websocket_clients": len(self.cm.active_connections) if self.cm else 0
            }
        }

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
