from fastapi import APIRouter, Depends, HTTPException, status
from app.services.services import get_feed_manager
from app.services.feed_manager import FeedManager
from app.models.websocket import GlobalRealtimeMetrics
from typing import Any
import logging

router = APIRouter()
logger = logging.getLogger(__name__)

@router.get(
    "/realtime",
    response_model=GlobalRealtimeMetrics,
    summary="Get Real-Time Analytics Metrics",
    description="Returns the latest real-time analytics metrics for the dashboard, including congestion index, average speed, active incidents, and feed statuses."
)
async def get_realtime_analytics(
    fm: FeedManager = Depends(get_feed_manager)
) -> GlobalRealtimeMetrics:
    """
    Returns the latest global real-time analytics metrics for the dashboard.
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