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
            # Demoted from HIGH -> NORMAL. Status updates were classified HIGH
            # under the assumption they were actionable control messages, but
            # they are pure telemetry (the frontend renders them, not acts on
            # them) and the HIGH priority queue is bounded -- under a stalled
            # sender they would queue up to maxsize and trigger 5s "queue
            # full" drops on every KPI broadcast. NORMAL keeps them flowing
            # through the same bounded backpressure as KPIs (which were
            # already NORMAL) without putting them ahead of actionable frames.
            await self._connection_manager.broadcast(msg.model_dump_json(), priority=MessagePriority.NORMAL)
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
            # NORMAL priority -- KPI updates are sub-second telemetry that the
            # frontend renders. The previous HIGH classification put them on
            # the bounded asyncio.PriorityQueue and a stalled sender would
            # block KPI delivery for 5s per message, accumulating "queue full"
            # drops. NORMAL keeps them flowing without competing for the
            # HIGH slot reserved for actionable control (CRITICAL/HIGH path).
            await self._connection_manager.broadcast_to_topic(
                message.model_dump_json(), topic='kpi', priority=MessagePriority.NORMAL
            )
        except Exception as e:
            logger.error(f"Error broadcasting KPI update: {e}", exc_info=True)

    async def broadcast_to_feed_realtime_bytes(self, feed_id: str, data: bytes, frame_index: int = 0):
        """
        Broadcasts binary frame data to subscribers of a specific feed.
        Delegates to ConnectionManager for prioritized delivery.
        """
        if not self._connection_manager:
            logger.warning("ConnectionManager is None; cannot broadcast realtime bytes.")
            return

        try:
            await self._connection_manager.broadcast_to_feed_realtime_bytes(feed_id, data, frame_index)
        except Exception as e:
            logger.error(f"Error broadcasting realtime bytes for {feed_id}: {e}", exc_info=True)

    async def broadcast_to_feed_realtime_bytes_adaptive(
        self, feed_id: str, full_data: bytes, small_data: bytes, frame_index: int = 0, latency_threshold_ms: float = 120
    ):
        """
        Latency-aware broadcast: full-res to low-RTT clients, downscaled to
        high-RTT (tunnel) clients. Delegates to ConnectionManager.
        """
        if not self._connection_manager:
            logger.warning("ConnectionManager is None; cannot broadcast adaptive realtime bytes.")
            return

        try:
            await self._connection_manager.broadcast_to_feed_realtime_bytes_adaptive(
                feed_id, full_data, small_data, frame_index, latency_threshold_ms
            )
        except Exception as e:
            logger.error(f"Error broadcasting adaptive realtime bytes for {feed_id}: {e}", exc_info=True)

