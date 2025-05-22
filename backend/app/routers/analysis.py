# backend/app/routers/analysis.py
from fastapi import APIRouter, Depends, HTTPException, Query, status, Body
from app.services.analytics_service import AnalyticsService
# Removed DatabaseManager, get_db as they are not used
from app.dependencies import get_current_active_user, get_as
# Removed SignalState as it's not used
# Added TrafficData, IncidentReport from traffic model
from app.models.traffic import AggregatedTrafficTrend, LocationModel, TrafficData, IncidentReport
from typing import List, Dict, Any, Optional # Keep Optional and List from typing
from datetime import datetime
from pydantic import BaseModel, Field # Added Field


class TrendDataPoint(BaseModel):
    timestamp: datetime
    total_vehicles: Optional[int] = None
    avg_speed: Optional[float] = None
    congestion_index: Optional[float] = None
    speeding_vehicles: Optional[int] = None
    high_density_lanes: Optional[int] = None


router = APIRouter()


class TrendQuery(BaseModel):
    start_time: datetime
    end_time: datetime
    region_id: Optional[str] = None
    sensor_ids: Optional[List[str]] = None
    # Corrected Field usage
    aggregation_interval_minutes: Optional[int] = Field(60, ge=5)


class AnomalyDetectionRequest(BaseModel):
    traffic_data_points: List[TrafficData]
    # Optional: context for detection like historical_period_to_compare


class IncidentPredictionRequest(BaseModel):
    location: LocationModel
    prediction_time: Optional[datetime] = None
    # Optional: specific conditions to simulate for prediction


@router.get(
    "/trends",
    response_model=AggregatedTrafficTrend,
    summary="Get Historical Trend Data or Generate Summary",
    dependencies=[Depends(get_current_active_user)]
)
async def get_analysis_trends(
    region_id: str = Query(..., description="Region ID for trend summary"),
    start_date: datetime = Query(..., description="Start date for summary (ISO 8601)"),
    end_date: datetime = Query(..., description="End date for summary (ISO 8601)"),
    analytics_svc: AnalyticsService = Depends(get_as)
) -> AggregatedTrafficTrend:
    """
    Generates a traffic trend summary for a given region and time period.
    """
    summary = await analytics_svc.generate_trend_summary(region_id, start_date, end_date)
    if not summary:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Could not generate trend summary for region {region_id}.")
    return summary


@router.post(
    "/detect-anomalies",
    response_model=List[IncidentReport],
    summary="Detect Traffic Anomalies",
    description="Processes traffic data points to detect anomalies and potential incidents.",
    dependencies=[Depends(get_current_active_user)]
)
async def detect_anomalies(
    request_data: AnomalyDetectionRequest = Body(...),
    analytics_svc: AnalyticsService = Depends(get_as)
) -> List[IncidentReport]:
    incidents = await analytics_svc.detect_traffic_anomalies(request_data.traffic_data_points)
    return incidents


@router.post(
    "/predict-incident-likelihood",
    response_model=Dict[str, Any],
    summary="Predict Incident Likelihood",
    description="Predicts incident likelihood at a given location and time.",
    dependencies=[Depends(get_current_active_user)]
)
async def predict_incident_likelihood_endpoint(
    request_data: IncidentPredictionRequest = Body(...),
    analytics_svc: AnalyticsService = Depends(get_as)
) -> Dict[str, Any]:
    prediction = await analytics_svc.predict_incident_likelihood(
        request_data.location, request_data.prediction_time
    )
    return prediction
