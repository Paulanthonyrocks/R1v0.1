from typing import Dict, Any
import logging
from app.websocket.connection_manager import ConnectionManager, MessagePriority
from app.models.websocket import (
    WebSocketMessage,
    WebSocketMessageTypeEnum,
    FeedStatusUpdate,
    GlobalRealtimeMetrics,
)
from app.models.feeds import FeedStatusData

logger = logging.getLogger("app.services.feed_broadcaster")

class FeedBroadcaster:
    """
    Handles all WebSocket broadcasting for the FeedManager.
    This service isolates the communication layer from the feed orchestration logic.
    """
    def __init__(self, connection_manager: ConnectionManager):
        self._connection_manager = connection_manager

    async def broadcast(self, message_type: WebSocketMessageTypeEnum, data: Dict[str, Any]):
        """Generic broadcast method for sending a message to all connected clients."""
        if not self._connection_manager:
            return
        
        try:
            message = WebSocketMessage(type=message_type, data=data)
            await self._connection_manager.broadcast(message.model_dump_json())
        except Exception as e:
            logger.error(f"Error during generic broadcast {message_type}: {e}", exc_info=True)

    async def broadcast_feed_update(self, status_data: FeedStatusData):
        """Broadcasts a status update for a specific feed."""
        if status_data is None:
            logger.warning("broadcast_feed_update called with None status_data; skipping.")
            return

        if not self._connection_manager:
            return
        
        try:
            msg = WebSocketMessage(
                type=WebSocketMessageTypeEnum.FEED_STATUS_UPDATE,
                data=FeedStatusUpdate(feed_status_data=status_data).model_dump(),
            )
            # HIGH priority so status updates are never dropped by video-frame back-pressure
            await self._connection_manager.broadcast(msg.model_dump_json(), priority=MessagePriority.HIGH)
        except Exception as e:
            logger.error(f"Error broadcasting feed update: {e}", exc_info=True)

    async def broadcast_kpi_update(self, kpi_data: GlobalRealtimeMetrics):
        """Broadcasts aggregated KPI metrics to the 'kpi' topic."""
        if not self._connection_manager:
            return
        
        try:
            message = WebSocketMessage(
                type=WebSocketMessageTypeEnum.KPI_UPDATE,
                data=kpi_data.model_dump(),
            )
            await self._connection_manager.broadcast_to_topic(
                message.model_dump_json(), topic='kpi', priority=MessagePriority.HIGH
            )
        except Exception as e:
            logger.error(f"Error broadcasting KPI update: {e}", exc_info=True)
