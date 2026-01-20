from typing import Optional, Dict
import logging
import json
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class AuditLogger:
    def __init__(self, db_manager):
        self._db = db_manager
    
    async def log_action(
        self,
        user_id: Optional[str],
        action: str,
        resource_type: str,
        resource_id: str,
        details: Optional[Dict] = None,
        ip_address: Optional[str] = None
    ):
        """Log user action for audit trail."""
        try:
            # We use _execute_write directly or a helper that ensures async compatibility
            # Since _execute_write is blocking/sync and uses lock, we wrap it in run_in_executor 
            # or just use the async wrapper if available. Here we assume we want to push to DB.
            # Assuming the existence of an 'audit_log' table.
            
            sql = """
            INSERT INTO audit_log 
            (user_id, action, resource_type, resource_id, details, ip_address, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """
            params = (
                user_id, 
                action, 
                resource_type, 
                resource_id,
                json.dumps(details) if details else None,
                ip_address,
                datetime.now(timezone.utc).timestamp() # SQLite often stores REAL for timestamps
            )
            
            # Using the synchronous _execute_write in a thread to avoid blocking main loop
            # This requires access to the underlying sync connection logic which _execute_write has.
            import asyncio
            await asyncio.to_thread(self._db._execute_write, sql, params)
            
        except Exception as e:
            # Audit logging failure should not crash the app, but must be logged
            logger.error(f"Failed to log audit event: {e}")

