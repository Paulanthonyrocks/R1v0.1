import logging
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from app.models import traffic # Import the traffic module
from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
import pandas as pd
import time

from app.dependency_injection import (
    get_analytics_service,
    get_advanced_analytics_service,
    get_db,
    get_db_manager,
)
from app.exceptions import OperationFailed
from app.models.alerts import Alert, AlertSeverityEnum # Import Alert and AlertSeverityEnum
from app.services.analytics_service_pro import AdvancedAnalyticsService
from app.utils.json_sanitize import to_jsonable
from app.models.analysis import (
 AnomalyDetectionRequest,
 AllNodesCongestionResponse,
 IncidentPredictionRequest,
 LocationPredictionRequest,
 PredictionResponse,
)
from app.models.common import APIResponse
from app.utils.database import DatabaseManager
from app.utils.planning_metrics import summarize_series_rows
from app.ml import traffic_analyzer # calculate_rolling_averages / detect_simple_anomaly (pure pandas, still used)
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
    average_vehicle_count: Optional[float] = None
    average_speed: Optional[float] = None
    speed_confidence: Optional[str] = None  # "high"|"low"|"unknown" from feed_metrics extra

class IdentifyTrafficPatternRequest(BaseModel):
    sensor_id: str
    time_range: str # e.g., 'rush_hour', 'midnight'

class TrafficPatternResponse(BaseModel):
    average_vehicle_count: Optional[float] = None
    average_speed: Optional[float] = None
    speed_confidence: Optional[str] = None

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
)
async def get_analysis_trends(
    region_id: str = Query(..., description="Region ID for trend summary generation"),
    start_date: datetime = Query(
        ..., description="Start date for trend summary (ISO 8601 format)"
    ),
    end_date: datetime = Query(
        ..., description="End date for trend summary (ISO 8601 format)"
    ),
    analytics_svc: AnalyticsService = Depends(get_analytics_service),
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
)
async def detect_anomalies(
    request_data: AnomalyDetectionRequest = Body(...),
    analytics_svc: AnalyticsService = Depends(get_analytics_service),
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
)
async def predict_incident_likelihood_endpoint(
    request_data: IncidentPredictionRequest = Body(...),
    analytics_svc: AnalyticsService = Depends(get_analytics_service),
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
)
async def get_location_predictions(
    request: LocationPredictionRequest,
    analytics_svc: AnalyticsService = Depends(get_analytics_service),
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
    analytics_service: AnalyticsService = Depends(
        get_analytics_service
    ),
) -> APIResponse[AllNodesCongestionResponse]:
    logger.info("GET /analytics/nodes/congestion endpoint called")
    """
    Retrieves the latest congestion data for all monitored nodes.
    Each node's data includes its ID, name, coordinates, congestion score,
    vehicle count, average speed, and the timestamp of the latest data.
    """
    try:
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
#
# Rewired 2026-11: these previously called db.get_collection('processed_traffic_data')
# -- a MongoDB-only method the active DatabaseManager does not implement, so every
# call raised and returned 500 (MongoDB is disabled in config). They now read the
# persisted feed_metrics table via DatabaseManager.get_feed_metrics_range, which is
# where the pipeline actually writes (per-frame counts/speeds + planning extras).

async def _fetch_metrics_series(
    db: DatabaseManager,
    sensor_ids: List[str],
    start_time: datetime,
    end_time: datetime,
) -> List[Dict[str, Any]]:
    """feed_metrics rows for the given sensors over [start_time, end_time]."""
    rows: List[Dict[str, Any]] = []
    for sid in sensor_ids:
        try:
            fetched = await db.get_feed_metrics_range(
                sid, start_time.timestamp(), end_time.timestamp()
            )
        except Exception as e:
            logger.warning(f"feed_metrics fetch failed for sensor {sid}: {e}")
            continue
        for row in fetched:
            row["sensor_id"] = sid
            rows.append(row)

    def _ts_key(r):
        ts = r.get("timestamp")
        if hasattr(ts, "timestamp"):
            return ts.timestamp()
        return ts or 0

    rows.sort(key=_ts_key)
    return rows


@router.post(
    "/analyze/average_traffic",
    response_model=APIResponse[AverageTrafficResponse],
    summary="Get Average Traffic Data",
    description="Calculates average vehicle count and speed for specified sensors within a time range.",
)
async def get_average_traffic(
    request_data: GetAverageTrafficRequest = Body(...),
    db: DatabaseManager = Depends(get_db_manager)
) -> APIResponse[AverageTrafficResponse]:
    logger.info(f"POST /analyze/average_traffic endpoint called with sensor_ids: {request_data.sensor_ids}, time range: {request_data.start_time} to {request_data.end_time}")
    try:
        rows = await _fetch_metrics_series(
            db, request_data.sensor_ids, request_data.start_time, request_data.end_time
        )
        summary = summarize_series_rows(rows)
        return APIResponse.success(
            data=AverageTrafficResponse(
                average_vehicle_count=summary["average_vehicle_count"],
                average_speed=summary["average_speed"],
                speed_confidence=summary["speed_confidence"],
            ),
            message="Average traffic data retrieved successfully.",
        )
    except Exception as e:
        logger.error(f"Error getting average traffic data: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get average traffic data: {e}")


@router.post(
    "/analyze/pattern",
    response_model=APIResponse[TrafficPatternResponse],
    summary="Identify Traffic Pattern",
    description="Identifies traffic pattern based on historical data for a given sensor and time range.",
)
async def identify_traffic_pattern_endpoint(
    request_data: IdentifyTrafficPatternRequest = Body(...),
    db: DatabaseManager = Depends(get_db_manager)
) -> APIResponse[TrafficPatternResponse]:
    logger.info(f"POST /analyze/pattern endpoint called for sensor_id: {request_data.sensor_id}, time_range: {request_data.time_range}")
    try:
        # Historical windows for the requested pattern (same windows the old
        # Mongo implementation used): rush_hour = 7-9am and 4-6pm on the past
        # 5 weekdays; midnight = 0-1am on the past 7 days.
        now = datetime.now(timezone.utc)
        windows: List = []
        if request_data.time_range == "rush_hour":
            for i in range(1, 6):
                past_day = now - timedelta(days=i)
                if past_day.weekday() < 5:
                    windows.append((past_day.replace(hour=7, minute=0, second=0, microsecond=0),
                                    past_day.replace(hour=9, minute=0, second=0, microsecond=0)))
                    windows.append((past_day.replace(hour=16, minute=0, second=0, microsecond=0),
                                    past_day.replace(hour=18, minute=0, second=0, microsecond=0)))
        elif request_data.time_range == "midnight":
            for i in range(1, 8):
                past_day = now - timedelta(days=i)
                windows.append((past_day.replace(hour=0, minute=0, second=0, microsecond=0),
                                past_day.replace(hour=1, minute=0, second=0, microsecond=0)))
        else:
            logger.warning(f"Unknown time_range pattern: {request_data.time_range}. Cannot identify pattern.")
            return APIResponse.success(
                data=TrafficPatternResponse(average_vehicle_count=None, average_speed=None),
                message=f"Unknown time_range pattern: {request_data.time_range}",
            )

        rows: List[Dict[str, Any]] = []
        for start_t, end_t in windows:
            rows.extend(await _fetch_metrics_series(db, [request_data.sensor_id], start_t, end_t))
        summary = summarize_series_rows(rows)
        return APIResponse.success(
            data=TrafficPatternResponse(
                average_vehicle_count=summary["average_vehicle_count"],
                average_speed=summary["average_speed"],
                speed_confidence=summary["speed_confidence"],
            ),
            message="Traffic pattern identified successfully.",
        )
    except Exception as e:
        logger.error(f"Error identifying traffic pattern: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to identify traffic pattern: {e}")


@router.post(
    "/analyze/anomaly",
    response_model=APIResponse[SimpleAnomalyDetectionResponse],
    summary="Detect Simple Anomaly",
    description="Detects a simple anomaly by comparing current data to historical patterns.",
)
async def detect_simple_anomaly_endpoint(
    request_data: SimpleAnomalyDetectionRequest = Body(...),
    analytics_service: AnalyticsService = Depends(get_analytics_service),
    db: DatabaseManager = Depends(get_db_manager)
) -> APIResponse[SimpleAnomalyDetectionResponse]:
    logger.info(f"POST /analyze/anomaly endpoint called with threshold: {request_data.threshold}")

    # --- Fetch historical time series data (last hour, persisted feed_metrics) ---
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(hours=1)
    window_size = '10min'

    logger.info(f"Fetching historical time series data for sensor {request_data.sensor_id} from {start_time} to {end_time}")
    rows = await _fetch_metrics_series(db, [request_data.sensor_id], start_time, end_time)

    if rows:
        df = pd.DataFrame([
            {
                "timestamp": r.get("timestamp"),
                "vehicle_count": r.get("vehicle_count") or 0,
                "average_speed": r.get("average_speed") or 0.0,
            }
            for r in rows
        ])
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df = df.set_index("timestamp").sort_index()
        logger.info(f"Calculating rolling averages with window size: {window_size}")
        df = traffic_analyzer.calculate_rolling_averages(df, window_size)
        # Historical pattern = mean of the fetched window (the old code passed
        # the rolling DataFrame itself to detect_simple_anomaly, whose .get()
        # on a DataFrame always returned None -> is_anomaly was always False).
        historical_pattern = {
            "average_vehicle_count": float(df["vehicle_count"].mean()),
            "average_speed": float(df["average_speed"].mean()),
        }
    else:
        logger.warning(f"No historical feed_metrics for sensor {request_data.sensor_id} in the last hour.")
        historical_pattern = {"average_vehicle_count": None, "average_speed": None}

    is_anomaly = traffic_analyzer.detect_simple_anomaly(
        request_data.current_data, historical_pattern, request_data.threshold
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
            message=f"Anomaly detected: Current data ({request_data.current_data}) deviates significantly from historical pattern.",
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
)
async def get_time_series_traffic_data(
    request_data: GetTimeSeriesRequest = Body(...),
    db: DatabaseManager = Depends(get_db_manager)
) -> APIResponse[TimeSeriesDataResponse]:
    logger.info(f"POST /analyze/time_series endpoint called with sensor_ids: {request_data.sensor_ids}, time range: {request_data.start_time} to {request_data.end_time}")
    if not request_data.sensor_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="At least one sensor_id must be provided.")

    try:
        rows = await _fetch_metrics_series(
            db, request_data.sensor_ids, request_data.start_time, request_data.end_time
        )
        time_series_data = []
        for r in rows:
            ts = r.get("timestamp")
            time_series_data.append(to_jsonable({
                "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else ts,
                "sensor_id": r.get("sensor_id"),
                "vehicle_count": r.get("vehicle_count"),
                "average_speed": r.get("average_speed"),
                "congestion_score": r.get("congestion_score"),
            }))

        return APIResponse.success(data=TimeSeriesDataResponse(data=time_series_data), message="Time series data retrieved successfully.")
    except Exception as e:
        logger.error(f"Error getting time series traffic data: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get time series traffic data: {e}")

@router.post(
    "/analyze/rolling_averages",
    response_model=APIResponse[RollingAveragesResponse],
    summary="Calculate Rolling Averages",
    description="Calculates rolling averages for vehicle count and average speed on provided time series data.",
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
        rolling_averages_data = to_jsonable(df_with_rolling_averages.reset_index().to_dict('records'))

        return APIResponse.success(data=RollingAveragesResponse(data=rolling_averages_data), message="Rolling averages calculated successfully.")
    except Exception as e:
        logger.error(f"Error calculating rolling averages: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to calculate rolling averages: {e}")

# --- Advanced Analytics (Pro) Endpoints ---

@router.get(
    "/od-matrix",
    summary="Get Origin-Destination Matrix",
    description="Calculates O-D matrix based on vehicles tracked across multiple feeds.",
)
async def get_od_matrix(
    hours: int = Query(1, ge=1, le=24),
    aas: AdvancedAnalyticsService = Depends(get_advanced_analytics_service)
):
    end_time = time.time()
    start_time = end_time - (hours * 3600)
    return await aas.get_origin_destination_matrix(start_time, end_time)


@router.get(
    "/od-matrix-roads",
    summary="Get road-keyed Origin-Destination Matrix",
    description="OD matrix keyed by road corridor position ('main-st@400m' -> "
                "'main-st@900m') using feed placement; unplaced feeds keep 'feed:<id>' keys.",
)
async def get_od_matrix_roads(
    hours: int = Query(1, ge=1, le=24),
    bucket_m: float = Query(100.0, gt=0, le=5000),
    aas: AdvancedAnalyticsService = Depends(get_advanced_analytics_service)
):
    end_time = time.time()
    start_time = end_time - (hours * 3600)
    return await aas.get_road_od_matrix(start_time, end_time, bucket_m)


@router.get(
    "/heatmap-roads",
    summary="Get Heatmap Data keyed to roads",
    description="Heatmap points projected to WGS84 via each feed's geo homography, "
                "then snapped to the road registry (road_id, chainage_m per point).",
)
async def get_heatmap_roads(
    feed_id: Optional[str] = Query(None),
    hours: int = Query(1, ge=1, le=24),
    max_points: int = Query(5000, ge=1, le=50000),
    aas: AdvancedAnalyticsService = Depends(get_advanced_analytics_service)
):
    return await aas.get_heatmap_road_data(feed_id, hours, max_points)

@router.get(
    "/travel-times",
    summary="Get Average Travel Times",
    description="Calculates average travel time between feed pairs.",
)
async def get_travel_times(
    hours: int = Query(1, ge=1, le=24),
    aas: AdvancedAnalyticsService = Depends(get_advanced_analytics_service)
):
    end_time = time.time()
    start_time = end_time - (hours * 3600)
    return await aas.get_average_travel_times(start_time, end_time)

@router.get(

    "/heatmap",

    summary="Get Heatmap Data",

    description="Returns raw points for heatmap generation.",


)

async def get_heatmap(

    feed_id: Optional[str] = Query(None),

    global_id: Optional[str] = Query(None),

    hours: int = Query(1, ge=1, le=24),

    aas: AdvancedAnalyticsService = Depends(get_advanced_analytics_service)

):

    return await aas.get_heatmap_data(feed_id, global_id, hours)



@router.get(

    "/forecast-vs-actual",

    summary="Get Forecast vs Actual Comparison",

    description="Returns time-series data comparing past predictions with actual outcomes.",


)

async def get_forecast_vs_actual(

    lat: float = Query(...),

    lon: float = Query(...),

    hours: int = Query(24, ge=1, le=168),

    as_svc: AnalyticsService = Depends(get_analytics_service)

):

    return await as_svc.get_forecast_vs_actual_data(lat, lon, hours)


@router.get(
    "/history/{feed_id}",
    summary="Get Simple History Stats",
    description="Get aggregated vehicle counts and speeds for a specific feed (for Dashboard charts).",
)
async def get_feed_history_endpoint(
    feed_id: str,
    hours: int = Query(24, ge=1, le=168),
):
    # NOTE: Previously this endpoint hard-depended on `get_db_manager`, which
    # raises HTTP 503 whenever the DatabaseManager isn't initialised (e.g. a
    # fresh boot, a transient DB hiccup, or TimescaleDB disabled). That turned
    # a routine "no history yet" state into a hard 503 that broke the dashboard
    # history widget. History is optional/eventually-consistent, so degrade
    # gracefully: return an empty list instead of 503ing. The DB layer's own
    # internal fallbacks (TimescaleDB -> SQLite vehicle_tracks) still apply.
    try:
        from app.database import get_database_manager
        db = get_database_manager()
    except RuntimeError as e:
        logger.warning(f"History requested for {feed_id} but DB unavailable: {e}")
        return []

    try:
        return await db.get_history_stats(feed_id, hours=hours)
    except Exception as e:
        # History is optional/eventually-consistent (see note above). Never let a
        # DB hiccup (TimescaleDB down, SQLite lock, transient error) hard-fail the
        # dashboard history widget. Degrade to an empty series instead of 500/503.
        logger.warning(f"History fetch failed for {feed_id}, returning empty: {e}")
        return []

@router.get("/emissions", summary="CO2 emissions estimate", tags=["Analytics"])
async def get_emissions(
    hours: int = Query(1, ge=1, le=168),
    as_svc: AnalyticsService = Depends(get_analytics_service),
):
    try:
        metrics_summary = await as_svc.get_metrics_summary()
        active_vehicles = metrics_summary.get("total_vehicles", 0) or 0
        avg_speed = metrics_summary.get("average_speed_kmh", 30)
        co2_rate = active_vehicles * float(avg_speed) * 0.12 / 3600
        return {
            "hourly_co2_kg": round(co2_rate, 3),
            "active_vehicles": int(active_vehicles),
            "avg_speed_kmh": float(avg_speed),
            "method": "backend_real_metrics_heuristic",
            "window_hours": hours,
        }
    except Exception as e:
        logger.warning(f"Emissions endpoint degraded: {e}")
        return {"hourly_co2_kg": 0.0, "method": "degraded", "error": str(e)}
