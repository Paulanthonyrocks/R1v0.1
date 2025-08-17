import logging
from typing import Any, Dict, List
from pydantic import BaseModel, Field
from app.models import traffic # Import the traffic module
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
import pandas as pd

from app.dependencies import get_analytics_service, get_as, get_current_active_user, get_db
from app.exceptions import OperationFailed
from app.models.alerts import Alert, AlertSeverityEnum # Import Alert and AlertSeverityEnum
from app.models.analysis import (
 AnomalyDetectionRequest,
 AllNodesCongestionResponse,
 IncidentPredictionRequest,
 LocationPredictionRequest,
 PredictionResponse,
)
from app.models.common import APIResponse
from app.utils.database import DatabaseManager
from app.ml import traffic_analyzer # Import the traffic_analyzer module
from app.services.analytics_service import AnalyticsService # Ensure AnalyticsService is imported
from datetime import datetime, timedelta, timezone # Import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

router = APIRouter()

# --- Pydantic Models for new endpoints ---

class GetAverageTrafficRequest(BaseModel):
 sensor_ids: List[str]
 start_time: datetime
 end_time: datetime

class AverageTrafficResponse(BaseModel):
 average_vehicle_count: float
 average_speed: float

class IdentifyTrafficPatternRequest(BaseModel):
 sensor_id: str
 time_range: str # e.g., 'rush_hour', 'midnight'

class TrafficPatternResponse(BaseModel):
 average_vehicle_count: float
 average_speed: float

class SimpleAnomalyDetectionRequest(BaseModel):
 current_data: Dict[str, float] = Field(..., description="Current traffic data with keys like 'vehicle_count' and 'average_speed'")
 sensor_id: str = Field(..., description="ID of the sensor for which to detect anomalies")
 threshold: float = Field(..., description="Threshold for anomaly detection")

class SimpleAnomalyDetectionResponse(BaseModel):
 is_anomaly: bool

class GetTimeSeriesRequest(BaseModel):
    sensor_ids: List[str]
    start_time: datetime
    end_time: datetime

# Using a generic Dict for time series data as DataFrame structure can vary
class TimeSeriesDataResponse(BaseModel):
    data: List[Dict[str, Any]]
    message: str = "Time series data retrieved successfully."

class CalculateRollingAveragesRequest(BaseModel):
    time_series_data: List[Dict[str, Any]]
    window_size: str

class RollingAveragesResponse(BaseModel):
    data: List[Dict[str, Any]]
    message: str = "Rolling averages calculated successfully."

# --- End Pydantic Models ---


# --- Existing Endpoints ---

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

# --- New Traffic Analysis Endpoints ---

@router.post(
    "/analyze/average_traffic",
    response_model=APIResponse[AverageTrafficResponse],
    summary="Get Average Traffic Data",
    description="Calculates average vehicle count and speed for specified sensors within a time range.",
    dependencies=[Depends(get_current_active_user)],
)
async def get_average_traffic(
    request_data: GetAverageTrafficRequest = Body(...),
    db: DatabaseManager = Depends(get_db) # Assuming get_db provides DatabaseManager
) -> APIResponse[AverageTrafficResponse]:
    logger.info(f"POST /analyze/average_traffic endpoint called with sensor_ids: {request_data.sensor_ids}, time range: {request_data.start_time} to {request_data.end_time}")
    try:
        # Assuming processed data is stored in a collection accessible via db.processed_data_collection
        # You might need to adjust this based on your DatabaseManager implementation
        processed_collection = db.get_collection('processed_traffic_data') # Example collection access
        
        avg_data = await traffic_analyzer.get_average_traffic_data(
            processed_collection,
            request_data.sensor_ids,
            request_data.start_time,
            request_data.end_time
        )
        return APIResponse.success(data=AverageTrafficResponse(**avg_data), message="Average traffic data retrieved successfully.")
    except Exception as e:
        logger.error(f"Error getting average traffic data: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get average traffic data: {e}")

@router.post(
    "/analyze/pattern",
    response_model=APIResponse[TrafficPatternResponse],
    summary="Identify Traffic Pattern",
    description="Identifies traffic pattern based on historical data for a given sensor and time range.",
    dependencies=[Depends(get_current_active_user)],
)
async def identify_traffic_pattern_endpoint(
    request_data: IdentifyTrafficPatternRequest = Body(...),
    db: DatabaseManager = Depends(get_db) # Assuming get_db provides DatabaseManager
) -> APIResponse[TrafficPatternResponse]:
    logger.info(f"POST /analyze/pattern endpoint called for sensor_id: {request_data.sensor_id}, time_range: {request_data.time_range}")
    try:
         # Assuming processed data is stored in a collection accessible via db.processed_data_collection
        processed_collection = db.get_collection('processed_traffic_data') # Example collection access

        pattern_data = await traffic_analyzer.identify_traffic_pattern(
            processed_collection,
            request_data.sensor_id,
            request_data.time_range
        )
        return APIResponse.success(data=TrafficPatternResponse(**pattern_data), message="Traffic pattern identified successfully.")
    except Exception as e:
        logger.error(f"Error identifying traffic pattern: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to identify traffic pattern: {e}")

@router.post(
    "/analyze/anomaly",
    response_model=APIResponse[SimpleAnomalyDetectionResponse],
    summary="Detect Simple Anomaly",
    description="Detects a simple anomaly by comparing current data to historical patterns.",
    dependencies=[Depends(get_current_active_user)],
)
async def detect_simple_anomaly_endpoint(
    request_data: SimpleAnomalyDetectionRequest = Body(...),
 analytics_service: AnalyticsService = Depends(get_as), # Add AnalyticsService dependency
 db: DatabaseManager = Depends(get_db) # Add DatabaseManager dependency
) -> APIResponse[SimpleAnomalyDetectionResponse]:
    logger.info(f"POST /analyze/anomaly endpoint called with threshold: {request_data.threshold}")

    # --- Fetch historical time series data ---
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=1) # Fetch data for the last 1 hour
    window_size = '10min' # Define rolling window size

    logger.info(f"Fetching historical time series data for sensor {request_data.sensor_id} from {start_time} to {end_time}")
    processed_collection = db.get_collection('processed_traffic_data') # Example collection access
    history_data_df = traffic_analyzer.get_time_series_data(
        processed_collection,
        request_data.sensor_id,
        start_time,
        end_time
    )

    logger.info(f"Calculating rolling averages with window size: {window_size}")
    history_data_df = traffic_analyzer.calculate_rolling_averages(history_data_df, window_size)

    # --- Detect simple anomaly using current data and historical time series with rolling averages ---
    is_anomaly = traffic_analyzer.detect_simple_anomaly(
 request_data.current_data, history_data_df, request_data.threshold
    )

    if is_anomaly:
        logger.warning("Anomaly detected! Creating an alert.")
        # Create an Alert instance
        alert_details = {
            "current_data": request_data.current_data,
            "threshold": request_data.threshold,
        }

        alert = Alert(
            # id will be generated by the database
            timestamp=datetime.now(timezone.utc), # Use timezone.utc
            severity=AlertSeverityEnum.WARNING, # Set appropriate severity
            message=f"Anomaly detected: Current data ({request_data.current_data}) deviates significantly from historical pattern ({request_data.historical_pattern_data}).",
            details=alert_details,
            source_component="traffic_anomaly_detection",
        )
        await analytics_service.create_and_save_alert(alert)

    return APIResponse.success(data=SimpleAnomalyDetectionResponse(is_anomaly=is_anomaly), message="Anomaly detection performed successfully.")

# --- New Time Series Analysis Endpoints ---

@router.post(
    "/analyze/time_series",
    response_model=APIResponse[TimeSeriesDataResponse],
    summary="Get Time Series Traffic Data",
    description="Retrieves time series traffic data for specified sensors within a time range.",
    dependencies=[Depends(get_current_active_user)],
)
async def get_time_series_traffic_data(
    request_data: GetTimeSeriesRequest = Body(...),
    db: DatabaseManager = Depends(get_db)
) -> APIResponse[TimeSeriesDataResponse]:
    logger.info(f"POST /analyze/time_series endpoint called with sensor_ids: {request_data.sensor_ids}, time range: {request_data.start_time} to {request_data.end_time}")
    if not request_data.sensor_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one sensor_id must be provided.")

    # traffic_analyzer.get_time_series_data expects a single sensor_id
    # For multiple sensor_ids, we'll process the first one for now or you can modify
    # the analyzer function to handle multiple sensors and potentially return a concatenated or grouped DataFrame.
    # For this implementation, we'll process the first sensor and log a warning if multiple are provided.
    sensor_id_to_process = request_data.sensor_ids[0]
    if len(request_data.sensor_ids) > 1:
        logger.warning(f"Multiple sensor_ids provided, processing only the first one: {sensor_id_to_process}")

    try:
        processed_collection = db.get_collection('processed_traffic_data')

        dataframe = traffic_analyzer.get_time_series_data(
            processed_collection,
            sensor_id_to_process,
            request_data.start_time,
            request_data.end_time
        )

        # Convert DataFrame to a list of dictionaries for JSON response
        time_series_data = dataframe.reset_index().to_dict('records')

        return APIResponse.success(data=TimeSeriesDataResponse(data=time_series_data), message="Time series data retrieved successfully.")
    except Exception as e:
        logger.error(f"Error getting time series traffic data: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get time series traffic data: {e}")

@router.post(
    "/analyze/rolling_averages",
    response_model=APIResponse[RollingAveragesResponse],
    summary="Calculate Rolling Averages",
    description="Calculates rolling averages for vehicle count and average speed on provided time series data.",
    dependencies=[Depends(get_current_active_user)],
)
async def calculate_rolling_averages_endpoint(
    request_data: CalculateRollingAveragesRequest = Body(...)
) -> APIResponse[RollingAveragesResponse]:
    logger.info(f"POST /analyze/rolling_averages endpoint called with window size: {request_data.window_size}")
    try:
        # Convert list of dictionaries back to DataFrame
        dataframe = pd.DataFrame(request_data.time_series_data).set_index('timestamp') # Assuming 'timestamp' is the index column name in the received data
        dataframe.index = pd.to_datetime(dataframe.index) # Ensure index is datetime objects

        df_with_rolling_averages = traffic_analyzer.calculate_rolling_averages(dataframe, request_data.window_size)
        rolling_averages_data = df_with_rolling_averages.reset_index().to_dict('records')

        return APIResponse.success(data=RollingAveragesResponse(data=rolling_averages_data), message="Rolling averages calculated successfully.")
    except Exception as e:
        logger.error(f"Error calculating rolling averages: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to calculate rolling averages: {e}")
