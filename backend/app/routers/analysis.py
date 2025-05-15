# backend/app/models/analysis.py
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel

class TrendDataPoint(BaseModel):
    timestamp: datetime
    total_vehicles: Optional[int] = None
    avg_speed: Optional[float] = None
    congestion_index: Optional[float] = None
    speeding_vehicles: Optional[int] = None
    high_density_lanes: Optional[int] = None

# backend/app/routers/analysis.py

from fastapi import APIRouter, Depends, HTTPException, Query, status, Body
from typing import List, Dict, Any, Optional
from datetime import datetime

# Models
from app.models.traffic import AggregatedTrafficTrend, LocationModel, TrafficData, IncidentReport # Added TrafficData, IncidentReport
from app.models.signals import SignalState # If analysis needs signal state

# Dependencies
from app.dependencies import get_db, get_current_active_user, get_as # Added get_as
from app.utils.utils import DatabaseManager
from app.services.analytics_service import AnalyticsService # For type hinting

router = APIRouter()

class TrendQuery(BaseModel):
    start_time: datetime
    end_time: datetime
    region_id: Optional[str] = None
    sensor_ids: Optional[List[str]] = None
    aggregation_interval_minutes: Optional[int] = Field(60, ge=5) # e.g., 60 for hourly

class AnomalyDetectionRequest(BaseModel):
    traffic_data_points: List[TrafficData]
    # Optional: context for detection like historical_period_to_compare

class IncidentPredictionRequest(BaseModel):
    location: LocationModel
    prediction_time: Optional[datetime] = None
    # Optional: specific conditions to simulate for prediction

@router.get(
    "/trends",
    # response_model=List[AggregatedTrafficTrend], # Keep old one for now, or make a new endpoint
    response_model=AggregatedTrafficTrend, # Changed to single for generate_trend_summary
    summary="Get Historical Trend Data or Generate Summary",
    # description="Retrieves aggregated traffic trend data or generates a new summary.",
    dependencies=[Depends(get_current_active_user)] # Protects the whole endpoint set
)
async def get_analysis_trends(
    # Parameters for querying existing trends from DB (matches old usage)
    # start_time: datetime = Query(..., description="Start timestamp (ISO 8601 format)"),
    # end_time: datetime = Query(..., description="End timestamp (ISO 8601 format)"),
    # db: DatabaseManager = Depends(get_db),
    # current_user: dict = Depends(get_current_active_user) # Already in dependencies

    # Parameters for generating a new trend summary (using AnalyticsService)
    region_id: str = Query(..., description="Region ID for trend summary generation"),
    start_date: datetime = Query(..., description="Start date for trend summary (ISO 8601 format)"),
    end_date: datetime = Query(..., description="End date for trend summary (ISO 8601 format)"),
    analytics_svc: AnalyticsService = Depends(get_as)
) -> AggregatedTrafficTrend:
    """
    Placeholder: Generates a traffic trend summary for a given region and time period.
    The old functionality of querying pre-aggregated trends from DB can be a separate endpoint or refined.
    """
    summary = await analytics_svc.generate_trend_summary(region_id, start_date, end_date)
    if not summary:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Could not generate trend summary for region {region_id}.")
    return summary

@router.post(
    "/detect-anomalies", 
    response_model=List[IncidentReport],
    summary="Detect Traffic Anomalies",
    description="Processes a list of traffic data points to detect anomalies and potential incidents.",
    dependencies=[Depends(get_current_active_user)]
)
async def detect_anomalies(
    request_data: AnomalyDetectionRequest = Body(...),
    analytics_svc: AnalyticsService = Depends(get_as)
) -> List[IncidentReport]:
    incidents = await analytics_svc.detect_traffic_anomalies(request_data.traffic_data_points)
    # Optionally, these incidents could be saved to a database here or by the service.
    return incidents

@router.post(
    "/predict-incident-likelihood", 
    response_model=Dict[str, Any], # Or a specific Pydantic model for the response
    summary="Predict Incident Likelihood",
    description="Predicts the likelihood of an incident at a given location and time.",
    dependencies=[Depends(get_current_active_user)]
)
async def predict_incident_likelihood_endpoint(
    request_data: IncidentPredictionRequest = Body(...),
    analytics_svc: AnalyticsService = Depends(get_as)
) -> Dict[str, Any]:
    prediction = await analytics_svc.predict_incident_likelihood(request_data.location, request_data.prediction_time)
    return prediction

# TODO:
# - Consider if the old /trends that queries DB for List[TrendDataPoint] should be kept as a separate endpoint.
# - The TrendDataPoint model was defined in analysis.py previously, needs to be added back or use AggregatedTrafficTrend.
#   For now, the old /trends functionality is commented out in favor of generate_trend_summary.