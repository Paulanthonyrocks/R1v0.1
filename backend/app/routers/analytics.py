from fastapi import APIRouter, Depends, HTTPException, status
from app.services.services import get_feed_manager
from app.services.feed_manager import FeedManager
from app.models.websocket import GlobalRealtimeMetrics
# Removed Any as it's not used directly in this file's type hints
import logging

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get(
    "/realtime",
    response_model=GlobalRealtimeMetrics,
    summary="Get Real-Time Analytics Metrics",
    description="Returns latest real-time analytics metrics for the dashboard."
)
async def get_realtime_analytics(
    fm: FeedManager = Depends(get_feed_manager)
) -> GlobalRealtimeMetrics:
    """
    Returns the latest global real-time analytics metrics.
    """
    try:
        # Logic replicated from FeedManager._broadcast_kpi_update
        async with fm._lock:
            running, error, idle, speeds = 0, 0, 0, []
            congestion_idx, incidents_kpi = 0.0, 0 # Placeholder for incidents

            for entry in fm.process_registry.values():
                status_val = entry['status']
                # Ensure status_val is FeedOperationalStatusEnum or convert
                # This part is complex due to dynamic enum loading, simplifying for now
                # Assuming status_val is already or can be converted to string safely
                status_str = str(status_val).lower()

                if status_str == "running":
                    running += 1
                    metrics = entry.get('latest_metrics')
                    if metrics and isinstance(metrics.get('avg_speed'), (int, float)):
                        speeds.append(float(metrics['avg_speed']))
                elif status_str == "error":
                    error += 1
                elif status_str == "stopped": # Consider other states like 'initializing' as idle too
                    idle += 1

            avg_speed = round(float(speeds[len(speeds)//2]), 1) if speeds else 0.0
            speed_limit = fm.config.get('speed_limit', 60)
            congestion_thresh = fm.config.get('incident_detection', {}).get('congestion_speed_threshold', 20)

            if avg_speed < congestion_thresh and running > 0:
                congestion_idx = round(max(0, min(100, 100 * (1 - (avg_speed / congestion_thresh)))), 1)
            elif speed_limit > 0 and running > 0:
                congestion_idx = round(max(0, min(100, 100 * (1 - (avg_speed / speed_limit)))), 1)

            payload = GlobalRealtimeMetrics(
                metrics_source="FeedManagerGlobalKPIs",
                congestion_index=congestion_idx,
                average_speed_kmh=avg_speed,
                active_incidents_count=incidents_kpi,
                feed_statuses={"running": running, "error": error, "stopped": idle}
            )
        return payload
    except Exception as e:
        logger.error(f"Failed to compute real-time analytics: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail="Failed to compute real-time analytics.")
