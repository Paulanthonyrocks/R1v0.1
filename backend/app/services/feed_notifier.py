# backend/app/services/feed_notifier.py

import logging
from typing import TYPE_CHECKING

# Pydantic models and enums
from app.models.feeds import FeedStatusData
from app.models.alerts import Alert
from app.models.websocket import (
    WebSocketMessage,
    WebSocketMessageTypeEnum,
    FeedStatusUpdate,
    NewAlertNotification,
    GlobalRealtimeMetrics, # Assuming this is the correct model for KPI data
)

# Forward reference for ConnectionManager to avoid circular imports if ConnectionManager also imports things from services
if TYPE_CHECKING:
    from app.websocket.connection_manager import ConnectionManager


logger = logging.getLogger(__name__)

class FeedNotifier:
    """
    Handles the construction and broadcasting of WebSocket messages to clients.
    """
    def __init__(self, connection_manager: 'ConnectionManager'):
        """
        Initializes the FeedNotifier.
        Args:
            connection_manager: An instance of ConnectionManager to handle WebSocket broadcasts.
        """
        self.connection_manager = connection_manager
        logger.info("FeedNotifier initialized.")

    async def notify_feed_status_update(self, feed_status: FeedStatusData):
        """
        Notifies clients about a feed status update.
        Args:
            feed_status: The Pydantic model containing the updated feed status.
        """
        if not feed_status.feed_id:
            logger.warning("Cannot notify feed status update: feed_id is missing from FeedStatusData.")
            return

        payload = FeedStatusUpdate(feed_data=feed_status)
        message = WebSocketMessage(
            event_type=WebSocketMessageTypeEnum.FEED_STATUS_UPDATE,
            payload=payload.model_dump() # Ensure payload is serialized (e.g. dict)
        )
        topic = f"feed:{feed_status.feed_id}"

        logger.debug(f"Broadcasting feed status update for feed_id '{feed_status.feed_id}' to topic '{topic}'.")
        try:
            await self.connection_manager.broadcast_to_topic(message.model_dump_json(), topic)
            logger.info(f"Successfully broadcasted feed status update for feed_id '{feed_status.feed_id}'.")
        except Exception as e:
            logger.error(f"Error broadcasting feed status update for feed_id '{feed_status.feed_id}': {e}", exc_info=True)

    async def notify_alert(self, alert: Alert):
        """
        Notifies clients about a new alert.
        Args:
            alert: The Pydantic model containing the alert information.
        """
        payload = NewAlertNotification(alert_data=alert)
        message = WebSocketMessage(
            event_type=WebSocketMessageTypeEnum.NEW_ALERT_NOTIFICATION,
            payload=payload.model_dump() # Ensure payload is serialized
        )

        general_alerts_topic = "alerts"
        logger.debug(f"Broadcasting new alert (ID: {alert.id}) to general topic '{general_alerts_topic}'.")
        try:
            await self.connection_manager.broadcast_to_topic(message.model_dump_json(), general_alerts_topic)
            logger.info(f"Successfully broadcasted alert (ID: {alert.id}) to general alerts topic.")
        except Exception as e:
            logger.error(f"Error broadcasting alert (ID: {alert.id}) to general alerts topic: {e}", exc_info=True)

        if alert.feed_id:
            feed_specific_alert_topic = f"feed_alerts:{alert.feed_id}"
            logger.debug(f"Broadcasting alert (ID: {alert.id}) for feed_id '{alert.feed_id}' to topic '{feed_specific_alert_topic}'.")
            try:
                await self.connection_manager.broadcast_to_topic(message.model_dump_json(), feed_specific_alert_topic)
                logger.info(f"Successfully broadcasted alert (ID: {alert.id}) for feed_id '{alert.feed_id}'.")
            except Exception as e:
                logger.error(f"Error broadcasting alert (ID: {alert.id}) for feed_id '{alert.feed_id}': {e}", exc_info=True)
        else:
            logger.debug(f"Alert (ID: {alert.id}) has no associated feed_id, not broadcasting to feed-specific alert topic.")


    async def notify_kpi_update(self, kpi_data: GlobalRealtimeMetrics):
        """
        Notifies clients about global Key Performance Indicator (KPI) updates.
        Args:
            kpi_data: The Pydantic model containing the KPI data.
        """
        # The payload for KPI update is directly the kpi_data model itself, as per description
        message = WebSocketMessage(
            event_type=WebSocketMessageTypeEnum.GLOBAL_REALTIME_METRICS_UPDATE,
            payload=kpi_data.model_dump() # Ensure payload is serialized
        )
        kpi_topic = "kpis"

        logger.debug(f"Broadcasting KPI update to topic '{kpi_topic}'.")
        try:
            await self.connection_manager.broadcast_to_topic(message.model_dump_json(), kpi_topic)
            logger.info("Successfully broadcasted KPI update.")
        except Exception as e:
            logger.error(f"Error broadcasting KPI update: {e}", exc_info=True)

# Example usage (for illustration, ConnectionManager would need to be mocked or a real instance passed)
if __name__ == "__main__":
    # This block is for illustrative purposes and won't run directly in the application.
    # It requires a mock or real ConnectionManager and asyncio event loop.

    logging.basicConfig(level=logging.DEBUG)

    class MockConnectionManager:
        async def broadcast_to_topic(self, message: str, topic: str):
            logger.info(f"MockConnectionManager: Broadcasting to topic '{topic}': {message}")

    async def main_test():
        manager = MockConnectionManager()
        notifier = FeedNotifier(connection_manager=manager) # type: ignore

        # Test feed status update
        sample_feed_status = FeedStatusData(
            feed_id="test_feed_001",
            status="OPERATIONAL", # Assuming FeedOperationalStatusEnum resolves to string
            source="rtsp://example.com/feed1",
            worker_pid=12345,
            last_update_time=time.time(),
            fps=30.0,
            resolution_width=1920,
            resolution_height=1080
        )
        await notifier.notify_feed_status_update(sample_feed_status)

        # Test alert notification
        from datetime import datetime
        sample_alert = Alert(
            id="alert_xyz789",
            feed_id="test_feed_001",
            timestamp=datetime.utcnow(),
            severity="CRITICAL",
            message="Test critical alert for feed 001!",
            details={"reason": "Simulated critical event"},
            acknowledged=False
        )
        await notifier.notify_alert(sample_alert)

        sample_general_alert = Alert(
            id="alert_abc123",
            timestamp=datetime.utcnow(),
            severity="WARNING",
            message="General system warning.",
            acknowledged=False
        )
        await notifier.notify_alert(sample_general_alert)


        # Test KPI update
        sample_kpi_data = GlobalRealtimeMetrics(
            total_active_feeds=5,
            total_vehicles_detected_global=150,
            average_processing_fps_global=25.5,
            active_alerts_count=3,
            system_cpu_usage=55.2,
            system_memory_usage=65.8
        )
        await notifier.notify_kpi_update(sample_kpi_data)

    # To run this example (if needed for standalone testing of this file):
    # import asyncio
    # import time # Add this import at the top if not already present
    # asyncio.run(main_test())
