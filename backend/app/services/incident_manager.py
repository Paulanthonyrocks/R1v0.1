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
        
        # Debouncing: { "feed_id_anomaly_details": timestamp }
        self._active_alerts = {}
        self._debounce_interval = self.config.get("incident_management", {}).get("debounce_interval", 300) # 5 mins
        
        logger.info("IncidentManager initialized.")

    def set_feed_manager(self, feed_manager):
        """Sets the feed manager to avoid circular imports."""
        self._feed_manager = feed_manager
        logger.info("FeedManager set in IncidentManager.")

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
        Includes debouncing logic to prevent duplicate reports for the same event.
        """
        try:
            # 1. Debounce check
            if not bypass_debounce and source_feed_id:
                # Create a key based on feed and incident characteristics
                # For safety alerts, we might use the vehicle ID or subtype
                det_key = details.get("subtype") or details.get("details") or description
                debounce_key = f"{source_feed_id}_{det_key}"
                
                now = time.time()
                last_time = self._active_alerts.get(debounce_key, 0)
                if now - last_time < self._debounce_interval:
                    logger.debug(f"Debounced duplicate incident for {source_feed_id}: {det_key}")
                    return None
                
                self._active_alerts[debounce_key] = now

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
                message.model_dump_json(),
                topic="incidents"
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

            logger.info(f"Successfully created incident {incident_id}: {description}")
            return incident_id

        except Exception as e:
            logger.error(f"Error in create_incident: {e}", exc_info=True)
            return None

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
                    message.model_dump_json(),
                    topic="incidents"
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
