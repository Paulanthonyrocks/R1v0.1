from fastapi import APIRouter, Depends, HTTPException, status
from typing import List, Optional, Any, ClassVar
from datetime import datetime
from pydantic import BaseModel, Field

# Assuming AnalyticsService will be correctly injectable
# If not, a similar pattern to get_feed_manager might be needed for AnalyticsService
from app.services.analytics_service import AnalyticsService
from app.dependencies import get_analytics_service # Assuming this will be created or exists
from app.services.feed_manager import FeedManager # Keep for existing endpoint
from app.models.websocket import GlobalRealtimeMetrics # Keep for existing endpoint
from app.dependencies import get_feed_manager, get_current_active_user # Keep for existing endpoint

import logging

router = APIRouter()
logger = logging.getLogger(__name__)

# Pydantic Models for Node Congestion Data
class NodeCongestionData(BaseModel):
    id: str = Field(..., description="Unique identifier for the node (e.g., 'lat,lon' string or a specific ID).")
    name: str = Field(..., description="Display name for the node.")
    latitude: float = Field(..., description="Latitude of the node.")
    longitude: float = Field(..., description="Longitude of the node.")
    congestion_score: Optional[float] = Field(None, description="Calculated congestion score for the node (0-100).")
    vehicle_count: Optional[int] = Field(None, description="Number of vehicles detected at the node.")
    average_speed: Optional[float] = Field(None, description="Average speed of vehicles at the node (km/h).")
    timestamp: datetime = Field(..., description="Timestamp of the latest data point for this node.")

    class Config:
        orm_mode = True # For potential direct mapping from ORM objects if ever needed
        # Ensure examples are generated in OpenAPI docs
        schema_extra = {
            "example": {
                "id": "34.0522,-118.2437",
                "name": "Node at (34.0522, -118.2437)",
                "latitude": 34.0522,
                "longitude": -118.2437,
                "congestion_score": 65.5,
                "vehicle_count": 50,
                "average_speed": 25.0,
                "timestamp": "2023-10-27T10:30:00Z"
            }
        }

class AllNodesCongestionResponse(BaseModel):
    nodes: List[NodeCongestionData]
    schema_extra: ClassVar[dict] = {
        'example': {
            'nodes': [
                {
                    'id': '34.0522,-118.2437',
                    'name': 'Node at (34.0522, -118.2437)',
                    'latitude': 34.0522,
                    'longitude': -118.2437,
                    'congestion_score': 65.5,
                    'vehicle_count': 50,
                    'average_speed': 25.0,
                    'timestamp': '2023-10-27T10:30:00Z'
                },
                {
                    'id': '40.7128,-74.0060',
                    'name': 'Node at (40.7128, -74.0060)',
                    'latitude': 40.7128,
                    'longitude': -74.0060,
                    'congestion_score': 30.2,
                    'vehicle_count': 20,
                    'average_speed': 45.0,
                    'timestamp': '2023-10-27T10:35:00Z'
                }
            ]
        }
    }


@router.get(
    "/realtime",
    response_model=GlobalRealtimeMetrics,
    summary="Get Real-Time Analytics Metrics",
    description="Returns the latest real-time analytics metrics for the dashboard, including congestion index, average speed, active incidents, and feed statuses."
)
async def get_realtime_analytics(
    current_user: dict = Depends(get_current_active_user),
    fm: FeedManager = Depends(get_feed_manager)
) -> GlobalRealtimeMetrics:
    """
    Returns the latest global real-time analytics metrics for the dashboard.
    Requires authentication.
    """
    try:
        # Replicate the logic from FeedManager._broadcast_kpi_update, but return the metrics directly
        async with fm._lock:
            running_feeds = 0
            error_feeds = 0
            idle_feeds = 0
            all_speeds = []
            congestion_index = 0.0
            active_incidents_kpi = 0  # Placeholder, can be improved if incident tracking is added

            for entry in fm.process_registry.values():
                current_status_val = entry['status']
                if isinstance(current_status_val, str):
                    try:
                        current_status_enum = fm.config.get('FeedOperationalStatusEnum', None)
                        if not current_status_enum:
                            from app.models.feeds import FeedOperationalStatusEnum
                            current_status_enum = FeedOperationalStatusEnum(current_status_val.lower())
                        else:
                            current_status_enum = current_status_enum(current_status_val.lower())
                    except Exception:
                        current_status_enum = None
                else:
                    current_status_enum = current_status_val

                if current_status_enum and current_status_enum.value == "running":
                    running_feeds += 1
                    metrics = entry.get('latest_metrics')
                    if metrics and isinstance(metrics.get('avg_speed'), (int, float)):
                        all_speeds.append(float(metrics['avg_speed']))
                elif current_status_enum and current_status_enum.value == "error":
                    error_feeds += 1
                elif current_status_enum and current_status_enum.value == "stopped":
                    idle_feeds += 1

            avg_speed_kpi = round(float(all_speeds[len(all_speeds)//2]), 1) if all_speeds else 0.0
            speed_limit_kpi = fm.config.get('speed_limit', 60)
            congestion_thresh = fm.config.get('incident_detection', {}).get('congestion_speed_threshold', 20)

            if avg_speed_kpi < congestion_thresh and running_feeds > 0:
                congestion_index = round(max(0, min(100, 100 * (1 - (avg_speed_kpi / congestion_thresh)))), 1)
            elif speed_limit_kpi > 0 and running_feeds > 0:
                congestion_index = round(max(0, min(100, 100 * (1 - (avg_speed_kpi / speed_limit_kpi)))), 1)

            metrics_payload = GlobalRealtimeMetrics(
                metrics_source="FeedManagerGlobalKPIs",
                congestion_index=congestion_index,
                average_speed_kmh=avg_speed_kpi,
                active_incidents_count=active_incidents_kpi,
                feed_statuses={
                    "running": running_feeds,
                    "error": error_feeds,
                    "stopped": idle_feeds
                }
            )
        return metrics_payload
    except Exception as e:
        logger.error(f"Failed to compute real-time analytics: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to compute real-time analytics metrics.")

def calculate_congestion_index(feeds: list) -> float:
    """Calculate global congestion index from all active feeds"""
    if not feeds:
        return 0.0
    
    congestion_values = [f.congestion_index for f in feeds if f.congestion_index is not None]
    return sum(congestion_values) / len(congestion_values) if congestion_values else 0.0

@router.get(
    "/nodes/congestion",
    response_model=AllNodesCongestionResponse, # Using the wrapper model
    summary="Get Congestion Data for All Monitored Nodes",
    description="Returns a list of all monitored locations/nodes with their latest congestion data, including vehicle count, average speed, and congestion score."
)
async def get_all_nodes_congestion_data(
    current_user: dict = Depends(get_current_active_user), # Assuming authentication is needed
    analytics_service: AnalyticsService = Depends(get_analytics_service) # Dependency injection
) -> AllNodesCongestionResponse:
    """
    Retrieves the latest congestion data for all monitored nodes.
    Each node's data includes its ID, name, coordinates, congestion score,
    vehicle count, average speed, and the timestamp of the latest data.
    Requires authentication.
    """
    try:
        logger.info(f"User {current_user.get('username')} requesting all nodes congestion data.")
        node_data_list = await analytics_service.get_all_location_congestion_data()

        # node_data_list from AnalyticsService is List[Dict[str, Any]]
        # Pydantic will validate each item against NodeCongestionData
        return AllNodesCongestionResponse(nodes=node_data_list)
    except Exception as e:
        logger.error(f"Error retrieving all nodes congestion data: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve node congestion data."
        )