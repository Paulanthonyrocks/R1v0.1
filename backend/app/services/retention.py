import os
import time
import logging
import asyncio
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger("app.services.retention")

class RetentionService:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.retention_config = config.get("retention", {})
        self.enabled = self.retention_config.get("enabled", True)
        self.check_interval = self.retention_config.get("check_interval_seconds", 3600) # Default 1 hour
        self.max_age_days = self.retention_config.get("max_age_days", 7)
        self.max_size_gb = self.retention_config.get("max_size_gb", 10)
        
        # Ensure we have absolute paths or relative to project root
        self.monitored_directories = self.retention_config.get("directories", [
            "backend/data/processed_videos",
            "backend/data/pavement_images",
            "backend/data/pavement_reports",
            "backend/data/snapshots",
            "backend/data/hard_negatives"
        ])
        
        # Per-directory age overrides (in days)
        # We populate this from defaults and allow config to override
        self.directory_age_limits = {}
        for dir_path in self.monitored_directories:
            if "snapshots" in dir_path:
                self.directory_age_limits[dir_path] = config.get("snapshot_retention_days", 7)
            elif "hard_negatives" in dir_path:
                self.directory_age_limits[dir_path] = 3
            elif "processed_videos" in dir_path:
                self.directory_age_limits[dir_path] = self.max_age_days
            else:
                self.directory_age_limits[dir_path] = self.max_age_days
        
        self._cleanup_task: Optional[asyncio.Task] = None

    def start(self):
        if not self.enabled:
            logger.info("Retention service is disabled.")
            return
        
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self.run_cleanup_loop())
            logger.info("Retention service started.")

    async def stop(self):
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.info("Retention service stopped.")

    async def run_cleanup_loop(self):
        logger.info(f"Retention cleanup loop active. Max age: {self.max_age_days} days, Max size: {self.max_size_gb} GB.")
        
        while True:
            try:
                # Run cleanup in a thread to avoid blocking the event loop
                await asyncio.to_thread(self.perform_cleanup)
            except Exception as e:
                logger.error(f"Error during retention cleanup: {e}", exc_info=True)
            
            await asyncio.sleep(self.check_interval)

    def perform_cleanup(self):
        for dir_path in self.monitored_directories:
            full_path = Path(dir_path)
            if not full_path.exists():
                logger.debug(f"Directory does not exist, skipping: {full_path}")
                continue
            
            # Determine age limit for this specific directory
            age_days = self.directory_age_limits.get(dir_path, self.max_age_days)
            
            logger.info(f"Cleaning up directory: {full_path} (Retention: {age_days} days)")
            self._cleanup_by_age(full_path, age_days)
            self._cleanup_by_size(full_path)
            
        # Clean up database records
        self._cleanup_database_records()

    def _cleanup_database_records(self):
        """Clean up old database records and handle deferred VACUUM."""
        try:
            from app.database import get_database_manager
            db = get_database_manager()
            
            # S4 Fix: Execute deferred VACUUM if flagged
            if getattr(db, '_needs_vacuum', False):
                try:
                    logger.info("Running deferred SQLite VACUUM (scheduled by retention service)...")
                    with db.lock:
                        with db._get_sqlite_connection() as conn:
                            conn.execute("VACUUM")
                    db._needs_vacuum = False
                    logger.info("SQLite VACUUM completed successfully.")
                except Exception as e:
                    logger.error(f"Deferred VACUUM failed: {e}")
            
            # Retention days
            audit_days = self.retention_config.get("audit_days", 90)
            analytics_days = self.retention_config.get("analytics_days", 30)
            
            cutoff_audit = time.time() - (audit_days * 86400)
            cutoff_analytics = time.time() - (analytics_days * 86400)
            
            # Since perform_cleanup runs in a thread, we can call sync methods or run async in loop
            # Here we use a helper to fire-and-forget or wait for async via run_coroutine_threadsafe if loop available
            # But perform_cleanup is called via to_thread from an async loop.
            
            # We can't easily await here if perform_cleanup is synchronous.
            # Best approach: Add specific cleanup tasks to the database manager 
            # and call them. For now, we'll assume we can execute a simple SQL via private method or similar.
            
            # Using _execute_write directly (it uses a lock and is thread-safe-ish for SQLite)
            
            # Clean Audit Log
            db._execute_write("DELETE FROM audit_log WHERE timestamp < ?", (cutoff_audit,))
            
            # Clean Analytics (assuming table exists, if not it will just fail/log error which is fine)
            # db._execute_write("DELETE FROM analytics_events WHERE timestamp < ?", (cutoff_analytics,))
            
            logger.info(f"Database cleanup performed. Audit logs older than {audit_days} days removed.")
            
        except Exception as e:
            logger.error(f"Database cleanup failed: {e}")

    def _cleanup_by_age(self, directory: Path, age_days: int):
        now = time.time()
        max_age_seconds = age_days * 86400
        
        files_deleted = 0
        for file_path in directory.glob("*"):
            if file_path.is_file():
                file_age = now - file_path.stat().st_mtime
                if file_age > max_age_seconds:
                    try:
                        file_path.unlink()
                        logger.info(f"Deleted old file: {file_path} (Age: {file_age/86400:.1f} days, Limit: {age_days} days)")
                        files_deleted += 1
                    except Exception as e:
                        logger.error(f"Failed to delete {file_path}: {e}")
        
        if files_deleted > 0:
            logger.info(f"Cleanup by age in {directory}: Deleted {files_deleted} files.")

    def _cleanup_by_size(self, directory: Path):
        files = []
        total_size = 0
        for file_path in directory.glob("*"):
            if file_path.is_file():
                stat = file_path.stat()
                files.append((file_path, stat.st_mtime, stat.st_size))
                total_size += stat.st_size
        
        max_size_bytes = self.max_size_gb * 1024 * 1024 * 1024
        if total_size <= max_size_bytes:
            return

        logger.info(f"Directory {directory} size ({total_size/1024/1024/1024:.2f} GB) exceeds limit ({self.max_size_gb} GB).")

        # Sort by mtime (oldest first)
        files.sort(key=lambda x: x[1])
        
        bytes_to_delete = total_size - max_size_bytes
        deleted_size = 0
        
        for file_path, _, size in files:
            if deleted_size >= bytes_to_delete:
                break
            try:
                file_path.unlink()
                deleted_size += size
                logger.info(f"Deleted file due to size limit: {file_path} ({size/1024/1024:.1f} MB)")
            except Exception as e:
                logger.error(f"Failed to delete {file_path}: {e}")
        
        logger.info(f"Cleanup by size in {directory}: Deleted {deleted_size/1024/1024:.1f} MB.")
