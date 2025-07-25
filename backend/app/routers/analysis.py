# backend/app/models/analysis.py
from typing import List, Dict, Any, Optional, ClassVar
from datetime import datetime, timezone
from pydantic import BaseModel, Field
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status, Body

from app.models import traffic
from app.models.common import APIResponse
from app.exceptions import OperationFailed
from app.dependencies import get_current_active_user, get_as, get_analytics_service
from app.services.analytics_service import AnalyticsService

logger = logging.getLogger(__name__)

router = APIRouter()

class TrendDataPoint(BaseModel):
    timestamp: datetime
    total_vehicles: Optional[int] = None
    avg_speed: Optional[float] = None
    congestion_index: Optional[float] = None
    speeding_vehicles: Optional[int] = None
    high_density_lanes: Optional[int] = None

class LocationPredictionRequest(BaseModel):
    location: traffic.LocationModel
    prediction_time: Optional[datetime] = None
    prediction_window_hours: Optional[int] = Field(default=24, ge=1, le=168)
    include_historical_context: Optional[bool] = Field(default=True)
    
class PredictionResponse(BaseModel):
    location: traffic.LocationModel
    prediction_time: datetime
    incident_likelihood: float = Field(..., ge=0, le=1)
    confidence_score: float = Field(..., ge=0, le=1)
    contributing_factors: List[str]
    recommendations: List[str]
    historical_context: Optional[Dict[str, Any]]

class TrendQuery(BaseModel):
    start_time: datetime
    end_time: datetime
    region_id: Optional[str] = None
    sensor_ids: Optional[List[str]] = None
    aggregation_interval_minutes: Optional[int] = Field(60, ge=5) # e.g., 60 for hourly

class AnomalyDetectionRequest(BaseModel):
    traffic_data_points: List[traffic.TrafficData]
    # Optional: context for detection like historical_period_to_compare

class IncidentPredictionRequest(BaseModel):
    location: traffic.LocationModel
    prediction_time: Optional[datetime] = None
    # Optional: specific conditions to simulate for prediction

@router.get(
    "/trends",
    # response_model=List[AggregatedTrafficTrend], # Keep old one for now, or make a new endpoint
    response_model=traffic.AggregatedTrafficTrend, # Changed to traffic.AggregatedTrafficTrend
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
) -> traffic.AggregatedTrafficTrend:
    logger.info(f"GET /trends endpoint called for region: {region_id}, start_date: {start_date}, end_date: {end_date}")
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
    response_model=List[traffic.IncidentReport],
    summary="Detect Traffic Anomalies",
    description="Processes a list of traffic data points to detect anomalies and potential incidents.",
    dependencies=[Depends(get_current_active_user)]
)
async def detect_anomalies(
    request_data: AnomalyDetectionRequest = Body(...),
    analytics_svc: AnalyticsService = Depends(get_as)
) -> List[traffic.IncidentReport]:
    logger.info(f"POST /detect-anomalies endpoint called with {len(request_data.traffic_data_points)} data points.")
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
    logger.info(f"POST /predict-incident-likelihood endpoint called for location: {request_data.location.dict()}")
    prediction = await analytics_svc.predict_incident_likelihood(request_data.location, request_data.prediction_time)
    return prediction

@router.post(
    "/predictions/location",
    response_model=PredictionResponse,
    summary="Get Detailed Traffic Predictions",
    description="Get detailed traffic predictions for a specific location, including historical context and recommendations.",
    dependencies=[Depends(get_current_active_user)]
)
async def get_location_predictions(
    request: LocationPredictionRequest,
    analytics_svc: AnalyticsService = Depends(get_as)
) -> PredictionResponse:
    """Get detailed traffic predictions for a location"""
    try:
        prediction = await analytics_svc.predict_incident_likelihood(
            location=request.location,
            prediction_time=request.prediction_time
        )
        return PredictionResponse(**prediction)
    except Exception as e:
        logger.error(f"Error getting predictions: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error generating predictions: {str(e)}"
        )



# Pydantic Models for Node Congestion Data
class NodeCongestionData(BaseModel):
    id: str = Field(..., description="Unique identifier for the node (e.g., 'lat,lon' string or a specific ID).")
    name: str = Field(..., description="Display name for the node.")
    latitude: float = Field(..., description="Latitude of the node.")
    longitude: float = Field(..., description="Longitude of the node.")
    congestion_score: Optional[float] = Field(None, description="Calculated congestion score for the node (0-100).")
    vehicle_count: Optional[int] = Field(None, description="Number of vehicles detected at the node.")
    average_speed: Optional[float] = Field(None, description="Average speed of vehicles at the node (km/h).")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="Timestamp of the latest data point for this node.")

    class Config:
        from_attributes = True  # Updated from orm_mode = True for Pydantic V2
        json_schema_extra = {  # Updated from schema_extra for Pydantic V2
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
    "/nodes/congestion",
    response_model=APIResponse[AllNodesCongestionResponse], # Using the wrapper model
    summary="Get Congestion Data for All Monitored Nodes",
    description="Returns a list of all monitored locations/nodes with their latest congestion data, including vehicle count, average speed, and congestion score."
)
async def get_all_nodes_congestion_data(
    current_user: dict = Depends(get_current_active_user), # Assuming authentication is needed
    analytics_service: 'AnalyticsService' = Depends(get_analytics_service) # Dependency injection
) -> APIResponse[AllNodesCongestionResponse]:
    logger.info(f"GET /analytics/nodes/congestion endpoint called by user: {current_user.get('email')}")
    """
    Retrieves the latest congestion data for all monitored nodes.
    Each node's data includes its ID, name, coordinates, congestion score,
    vehicle count, average speed, and the timestamp of the latest data.
    Requires authentication.
    """
    try:
        logger.info(f"User {current_user.get('email')} requesting all nodes congestion data.")
        node_data_list = await analytics_service.get_all_location_congestion_data()
        logger.debug(f"Retrieved node data list: {node_data_list}")
        if not node_data_list:
            logger.warning("Analytics service returned an empty list for node congestion data.")

        # node_data_list from AnalyticsService is List[Dict[str, Any]]
        # Pydantic will validate each item against NodeCongestionData
        return APIResponse.success(data=AllNodesCongestionResponse(nodes=node_data_list), message="Successfully retrieved node congestion data.")
    except Exception as e:
        logger.error(f"Error retrieving all nodes congestion data: {e}", exc_info=True)
        raise OperationFailed(detail="Failed to retrieve node congestion data.")
# TODO:
# - Consider if the old /trends that queries DB for List[TrendDataPoint] should be kept as a separate endpoint.
# - The TrendDataPoint model was defined in analysis.py previously, needs to be added back or use AggregatedTrafficTrend.
#   For now, the old /trends functionality is commented out in favor of generate_trend_summary.