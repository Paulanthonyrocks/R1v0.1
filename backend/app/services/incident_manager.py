import logging
import asyncio
import uuid
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from app.models.traffic import IncidentTypeEnum, IncidentSeverityEnum, IncidentStatusEnum
from app.models.websocket import WebSocketMessage, WebSocketMessageTypeEnum
from app.websocket.connection_manager import ConnectionManager
from app.services.notification_service import NotificationService

logger = logging.getLogger("app.services.incident_manager")

class IncidentManager:
    def __init__(
        self,
        config: Dict[str, Any],
        db_manager,
        connection_manager: ConnectionManager,
        notification_service: Optional[NotificationService] = None,
    ):
        self.config = config
        self._db_manager = db_manager
        self._connection_manager = connection_manager
        self._notification_service = notification_service
        self._feed_manager = None
        
        # Stateful Tracking
        # { debounce_key: { "incident_id": str, "last_seen": timestamp, "status": IncidentStatusEnum } }
        self._active_incidents: Dict[str, Dict] = {}
        self._debounce_interval = self.config.get("incident_management", {}).get("debounce_interval", 300) # 5 mins
        
        # Background task for auto-resolving stale incidents
        self._cleanup_task = None
        
        logger.debug("IncidentManager initialized.")

    def set_feed_manager(self, feed_manager):
        """Sets the feed manager and starts background tasks."""
        self._feed_manager = feed_manager
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._auto_resolve_loop())
        logger.debug("FeedManager set in IncidentManager.")

    async def _auto_resolve_loop(self):
        """Periodically clears active incident tracking for items not seen recently."""
        while True:
            try:
                await asyncio.sleep(60)
                now = time.time()
                to_remove = []
                for key, data in self._active_incidents.items():
                    # If not seen for 10 minutes, stop tracking it as "active"
                    # This doesn't necessarily RESOLVE it in the DB, just allows new alerts
                    if now - data["last_seen"] > 600:
                        to_remove.append(key)
                
                for key in to_remove:
                    del self._active_incidents[key]
                
                # Hard limit on total size if cleanup didn't catch enough
                if len(self._active_incidents) > 1000:
                    # Remove oldest 200
                    sorted_keys = sorted(self._active_incidents.keys(), key=lambda k: self._active_incidents[k]["last_seen"])
                    for k in sorted_keys[:200]:
                        del self._active_incidents[k]
            except Exception as e:
                logger.error(f"Error in incident cleanup loop: {e}")

    async def create_incident(
        self,
        location: Dict[str, Any],
        incident_type: IncidentTypeEnum,
        severity: IncidentSeverityEnum,
        description: str,
        source_feed_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        bypass_debounce: bool = False
    ) -> Optional[str]:
        """
        Creates and saves a new incident report.
        Includes stateful deduplication to prevent duplicate reports for the same event.
        """
        try:
            # 1. Stateful Deduplication
            debounce_key = None
            if not bypass_debounce and source_feed_id:
                # Create a robust key based on feed, type, and specific entity (e.g. vehicle_id)
                entity_id = details.get("vehicle_id") or details.get("id") or "scene"
                inc_type_str = incident_type.value if hasattr(incident_type, "value") else str(incident_type)
                debounce_key = f"{source_feed_id}_{inc_type_str}_{entity_id}"
                
                now = time.time()
                if debounce_key in self._active_incidents:
                    active = self._active_incidents[debounce_key]
                    active["last_seen"] = now
                    logger.debug(f"Deduplicated active incident for {debounce_key}")
                    return active["incident_id"]

            # 2. Construct Incident Data
            incident_id = str(uuid.uuid4())
            now_dt = datetime.now(timezone.utc)
            
            incident_data = {
                "id": incident_id,
                "feed_id": source_feed_id,
                "type": incident_type.value if hasattr(incident_type, "value") else incident_type,
                "severity": severity.value if hasattr(severity, "value") else severity,
                "description": description,
                "status": IncidentStatusEnum.REPORTED.value,
                "timestamp": time.time(),
                "created_at": now_dt,
                "updated_at": now_dt,
                "latitude": location.get("latitude"),
                "longitude": location.get("longitude"),
                "snapshot_path": (details.get("snapshot_path") if details else None) or (details.get("meta", {}).get("snapshot_path") if details else None),
                "assigned_to": None,
                "resolution_notes": None
            }

            # 3. Persist to DB
            success = await self._db_manager.create_incident(incident_data)
            if not success:
                logger.error(f"Failed to persist incident {incident_id} to database.")
                return None

            # Record in active incidents map
            if debounce_key:
                self._active_incidents[debounce_key] = {
                    "incident_id": incident_id,
                    "last_seen": time.time(),
                    "status": IncidentStatusEnum.REPORTED
                }

            # 4. Log Audit Event
            await self.log_audit_event(
                user_id="SYSTEM",
                action="CREATE",
                resource_type="INCIDENT",
                resource_id=incident_id,
                details=f"Incident created: {description}"
            )

            # 5. Broadcast via WebSocket
            message = WebSocketMessage(
                type=WebSocketMessageTypeEnum.GENERAL_NOTIFICATION,
                data={
                    "message_type": "new_incident",
                    "title": f"New {incident_data['type']} Incident",
                    "message": description,
                    "severity": incident_data["severity"],
                    "incident_id": incident_id,
                    "feed_id": source_feed_id
                }
            )
            await self._connection_manager.broadcast_to_topic(
                "incidents",
                message.model_dump()
            )

            # 6. External Notifications
            if self._notification_service:
                # Run in background to not block the pipeline
                asyncio.create_task(self._notification_service.notify_incident(incident_data))

            # 7. Request Snapshot
            if source_feed_id and self._feed_manager:
                try:
                    await self._feed_manager.request_snapshot(source_feed_id, incident_id)
                except Exception as e:
                    logger.warning(f"Failed to request snapshot for incident {incident_id}: {e}")

            logger.debug(f"Successfully created incident {incident_id}: {description}")
            return incident_id

        except Exception as e:
            logger.error(f"Error in create_incident: {e}", exc_info=True)
            return None

    async def attach_snapshot(self, incident_id: str, snapshot_path: str):
        """Associates a high-res snapshot with an existing incident."""
        try:
            success = await self._db_manager.update_incident(incident_id, {
                "snapshot_path": snapshot_path,
                "updated_at": datetime.now(timezone.utc).isoformat()
            })
            if success:
                logger.debug(f"Attached snapshot to incident {incident_id}: {snapshot_path}")
                
                # Broadcast update so UI can show the snapshot
                message = WebSocketMessage(
                    type=WebSocketMessageTypeEnum.GENERAL_NOTIFICATION,
                    data={
                        "message_type": "incident_snapshot_ready",
                        "incident_id": incident_id,
                        "snapshot_path": snapshot_path
                    }
                )
                await self._connection_manager.broadcast_to_topic(
                    "incidents",
                    message.model_dump()
                )
        except Exception as e:
            logger.error(f"Failed to attach snapshot to incident {incident_id}: {e}")

    async def update_status(
        self,
        incident_id: str,
        new_status: IncidentStatusEnum,
        user_id: str = "SYSTEM",
        notes: Optional[str] = None
    ) -> bool:
        """
        Updates the status of an incident and logs the transition.
        """
        try:
            existing = await self._db_manager.get_incident_by_id(incident_id)
            if not existing:
                logger.warning(f"Attempted to update non-existent incident {incident_id}")
                return False

            old_status = existing.get("status")
            if old_status == new_status.value:
                return True

            updates = {
                "status": new_status.value,
                "updated_at": datetime.now(timezone.utc).isoformat()
            }
            if notes:
                updates["resolution_notes"] = notes

            success = await self._db_manager.update_incident(incident_id, updates)
            if success:
                # Update status in active tracking
                for key, data in list(self._active_incidents.items()):
                    if data["incident_id"] == incident_id:
                        if new_status in [IncidentStatusEnum.RESOLVED, IncidentStatusEnum.FALSE_POSITIVE]:
                            del self._active_incidents[key]
                        else:
                            data["status"] = new_status
                        break

                await self.log_audit_event(
                    user_id=user_id,
                    action="STATUS_CHANGE",
                    resource_type="INCIDENT",
                    resource_id=incident_id,
                    details=f"Status changed from {old_status} to {new_status.value}. Notes: {notes}"
                )
                
                # Broadcast update
                message = WebSocketMessage(
                    type=WebSocketMessageTypeEnum.GENERAL_NOTIFICATION,
                    data={
                        "message_type": "incident_update",
                        "incident_id": incident_id,
                        "status": new_status.value,
                        "notes": notes
                    }
                )
                await self._connection_manager.broadcast_to_topic(
                    "incidents",
                    message.model_dump()
                )
                return True
            return False
        except Exception as e:
            logger.error(f"Error updating incident status: {e}")
            return False

    async def assign_incident(self, incident_id: str, user_id: str) -> bool:
        """Assigns an incident to a specific user/operator."""
        try:
            success = await self._db_manager.update_incident(incident_id, {
                "assigned_to": user_id,
                "updated_at": datetime.now(timezone.utc).isoformat()
            })
            if success:
                await self.log_audit_event(
                    user_id="SYSTEM",
                    action="ASSIGN",
                    resource_type="INCIDENT",
                    resource_id=incident_id,
                    details=f"Incident assigned to user: {user_id}"
                )
                return True
            return False
        except Exception as e:
            logger.error(f"Error assigning incident: {e}")
            return False

    async def log_audit_event(
        self,
        user_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: str,
        ip_address: Optional[str] = None
    ):
        """Records an event in the audit log."""
        sql = """
        INSERT INTO audit_log (user_id, action, resource_type, resource_id, details, ip_address, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        params = (user_id, action, resource_type, resource_id, details, ip_address, time.time())
        try:
            # DatabaseManager._execute_write is synchronous, we run in thread
            await asyncio.to_thread(self._db_manager._execute_write, sql, params)
        except Exception as e:
            logger.error(f"Failed to log audit event: {e}")
