import logging
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from app.websocket.connection_manager import ConnectionManager
from app.dependency_injection import get_connection_manager

logger = logging.getLogger(__name__)
router = APIRouter()

class ConnectionStats(BaseModel):
    total_connections: int
    connections_by_role: Dict[str, int]
    total_subscriptions: int
    feed_subscriptions: Dict[str, int]
    average_queue_size: float
    max_queue_size: int
    active_client_latencies: Dict[str, float]

@router.get("/health", response_model=ConnectionStats)
async def get_websocket_health(
    connection_manager: ConnectionManager = Depends(get_connection_manager)
):
    """
    Get real-time WebSocket connection statistics and health metrics.
    Useful for infrastructure monitoring and debugging.
    """
    try:
        active_conn = connection_manager.active_connections
        
        # Connections by user role
        connections_by_role = {}
        for client_id in active_conn.keys():
            role = connection_manager.get_user_role(client_id)
            connections_by_role[role] = connections_by_role.get(role, 0) + 1
        
        # Feed subscriptions
        feed_subs = {
            feed_id: len(clients) 
            for feed_id, clients in connection_manager.feed_subscriptions.items()
        }
        
        # Queue metrics
        queue_sizes = [
            queue.qsize() 
            for queue in connection_manager.client_queues.values()
        ]
        
        # Total topic subscriptions
        total_topic_subs = sum(
            len(topics) 
            for topics in connection_manager.client_id_to_topics.values()
        )
        
        return ConnectionStats(
            total_connections=len(active_conn),
            connections_by_role=connections_by_role,
            total_subscriptions=total_topic_subs,
            feed_subscriptions=feed_subs,
            average_queue_size=sum(queue_sizes) / len(queue_sizes) if queue_sizes else 0,
            max_queue_size=max(queue_sizes) if queue_sizes else 0,
            active_client_latencies=connection_manager.client_latencies or {}
        )
    except Exception as e:
        logger.error(f"Error gathering WebSocket health stats: {e}")
        return ConnectionStats(
            total_connections=0,
            connections_by_role={},
            total_subscriptions=0,
            feed_subscriptions={},
            average_queue_size=0,
            max_queue_size=0,
            active_client_latencies={}
        )
