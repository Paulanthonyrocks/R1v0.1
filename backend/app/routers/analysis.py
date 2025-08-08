import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status

from app.dependencies import get_analytics_service, get_as, get_current_active_user
from app.exceptions import OperationFailed
from app.models import traffic
from app.models.analysis import (
    AllNodesCongestionResponse,
    AnomalyDetectionRequest,
    IncidentPredictionRequest,
    LocationPredictionRequest,
    PredictionResponse,
)
from app.models.common import APIResponse
from app.services.analytics_service import AnalyticsService
from datetime import datetime

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/trends",
    response_model=traffic.AggregatedTrafficTrend,
    summary="Get Historical Trend Data or Generate Summary",
    dependencies=[Depends(get_current_active_user)],
)
async def get_analysis_trends(
    region_id: str = Query(..., description="Region ID for trend summary generation"),
    start_date: datetime = Query(
        ..., description="Start date for trend summary (ISO 8601 format)"
    ),
    end_date: datetime = Query(
        ..., description="End date for trend summary (ISO 8601 format)"
    ),
    analytics_svc: AnalyticsService = Depends(get_as),
) -> traffic.AggregatedTrafficTrend:
    logger.info(
        f"GET /trends endpoint called for region: {region_id}, start_date: {start_date}, end_date: {end_date}"
    )
    summary = await analytics_svc.generate_trend_summary(
        region_id, start_date, end_date
    )
    if not summary:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Could not generate trend summary for region {region_id}.",
        )
    return summary


@router.post(
    "/detect-anomalies",
    response_model=List[traffic.IncidentReport],
    summary="Detect Traffic Anomalies",
    description="Processes a list of traffic data points to detect anomalies and potential incidents.",
    dependencies=[Depends(get_current_active_user)],
)
async def detect_anomalies(
    request_data: AnomalyDetectionRequest = Body(...),
    analytics_svc: AnalyticsService = Depends(get_as),
) -> List[traffic.IncidentReport]:
    logger.info(
        f"POST /detect-anomalies endpoint called with {len(request_data.traffic_data_points)} data points."
    )
    incidents = await analytics_svc.detect_traffic_anomalies(
        request_data.traffic_data_points
    )
    return incidents


@router.post(
    "/predict-incident-likelihood",
    response_model=Dict[str, Any],
    summary="Predict Incident Likelihood",
    description="Predicts the likelihood of an incident at a given location and time.",
    dependencies=[Depends(get_current_active_user)],
)
async def predict_incident_likelihood_endpoint(
    request_data: IncidentPredictionRequest = Body(...),
    analytics_svc: AnalyticsService = Depends(get_as),
) -> Dict[str, Any]:
    logger.info(
        f"POST /predict-incident-likelihood endpoint called for location: {request_data.location.dict()}"
    )
    prediction = await analytics_svc.predict_incident_likelihood(
        request_data.location, request_data.prediction_time
    )
    return prediction


@router.post(
    "/predictions/location",
    response_model=PredictionResponse,
    summary="Get Detailed Traffic Predictions",
    description="Get detailed traffic predictions for a specific location, including historical context and recommendations.",
    dependencies=[Depends(get_current_active_user)],
)
async def get_location_predictions(
    request: LocationPredictionRequest,
    analytics_svc: AnalyticsService = Depends(get_as),
) -> PredictionResponse:
    """Get detailed traffic predictions for a location"""
    try:
        prediction = await analytics_svc.predict_incident_likelihood(
            location=request.location, prediction_time=request.prediction_time
        )
        return PredictionResponse(**prediction)
    except Exception as e:
        logger.error(f"Error getting predictions: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error generating predictions: {str(e)}"
        )


@router.get(
    "/nodes/congestion",
    response_model=APIResponse[AllNodesCongestionResponse],
    summary="Get Congestion Data for All Monitored Nodes",
    description="Returns a list of all monitored locations/nodes with their latest congestion data, including vehicle count, average speed, and congestion score.",
)
async def get_all_nodes_congestion_data(
    current_user: dict = Depends(
        get_current_active_user
    ),
    analytics_service: "AnalyticsService" = Depends(
        get_analytics_service
    ),
) -> APIResponse[AllNodesCongestionResponse]:
    logger.info(
        f"GET /analytics/nodes/congestion endpoint called by user: {current_user.get('email')}"
    )
    """
    Retrieves the latest congestion data for all monitored nodes.
    Each node's data includes its ID, name, coordinates, congestion score,
    vehicle count, average speed, and the timestamp of the latest data.
    Requires authentication.
    """
    try:
        logger.info(
            f"User {current_user.get('email')} requesting all nodes congestion data."
        )
        node_data_list = await analytics_service.get_all_location_congestion_data()
        logger.debug(f"Retrieved node data list: {node_data_list}")
        if not node_data_list:
            logger.warning(
                "Analytics service returned an empty list for node congestion data."
            )

        return APIResponse.success(
            data=AllNodesCongestionResponse(nodes=node_data_list),
            message="Successfully retrieved node congestion data.",
        )
    except Exception as e:
        logger.error(f"Error retrieving all nodes congestion data: {e}", exc_info=True)
        raise OperationFailed(detail="Failed to retrieve node congestion data.")
