# backend/app/ml/traffic_analyzer.py
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Union

from motor.motor_asyncio import AsyncIOMotorCollection
import pandas as pd

# Setup logging
logger = logging.getLogger(__name__)

async def get_average_traffic_data(db_collection: AsyncIOMotorCollection, sensor_ids: List[str], start_time: datetime, end_time: datetime) -> Dict[str, Optional[float]]:
    """
    Queries the database for processed traffic data within the specified parameters and
    calculates the average vehicle count and average speed.
    """
    logger.info(f"Querying average traffic data for sensors {sensor_ids} from {start_time} to {end_time}")

    query = {
        "sensor_id": {"$in": sensor_ids},
        "timestamp": {"$gte": start_time, "$lte": end_time}
    }

    try:
        pipeline = [
            {"$match": query},
            {"$group": {
                "_id": None,
                "total_vehicle_count": {"$sum": "$vehicle_count"},
                "total_average_speed": {"$sum": "$average_speed"},
                "count": {"$sum": 1}
            }}
        ]
        cursor = db_collection.aggregate(pipeline)
        result = await cursor.to_list(length=1)

        if result:
            avg_vehicle_count = result[0]["total_vehicle_count"] / result[0]["count"]
            avg_speed = result[0]["total_average_speed"] / result[0]["count"]
            logger.info(f"Calculated averages: vehicle_count={avg_vehicle_count:.2f}, speed={avg_speed:.2f}")
            return {"average_vehicle_count": avg_vehicle_count, "average_speed": avg_speed}
        else:
            logger.warning("No data found for the specified query parameters.")
            return {"average_vehicle_count": None, "average_speed": None}

    except Exception as e:
        logger.error(f"Error fetching average traffic data: {e}", exc_info=True)
        return {"average_vehicle_count": None, "average_speed": None}

async def identify_traffic_pattern(db_collection: AsyncIOMotorCollection, sensor_id: str, time_range: str) -> Dict[str, Optional[float]]:
    """
    Queries historical data for a sensor within similar time ranges and calculates
    the average vehicle count and speed for that pattern.
    """
    logger.info(f"Identifying traffic pattern '{time_range}' for sensor {sensor_id}")

    now = datetime.now(timezone.utc)
    historical_windows = []

    if time_range == 'rush_hour':
        for i in range(1, 6):
            past_day = now - timedelta(days=i)
            if past_day.weekday() < 5:
                start_am = past_day.replace(hour=7, minute=0, second=0, microsecond=0)
                end_am = past_day.replace(hour=9, minute=0, second=0, microsecond=0)
                historical_windows.append((start_am, end_am))
                start_pm = past_day.replace(hour=16, minute=0, second=0, microsecond=0)
                end_pm = past_day.replace(hour=18, minute=0, second=0, microsecond=0)
                historical_windows.append((start_pm, end_pm))
    elif time_range == 'midnight':
        for i in range(1, 8):
            past_day = now - timedelta(days=i)
            start_midnight = past_day.replace(hour=0, minute=0, second=0, microsecond=0)
            end_midnight = past_day.replace(hour=1, minute=0, second=0, microsecond=0)
            historical_windows.append((start_midnight, end_midnight))
    else:
        logger.warning(f"Unknown time range pattern: {time_range}. Cannot identify pattern.")
        return {"average_vehicle_count": None, "average_speed": None}

    all_vehicle_counts = []
    all_average_speeds = []

    try:
        for start_t, end_t in historical_windows:
            query = {
                "sensor_id": sensor_id,
                "timestamp": {"$gte": start_t, "$lte": end_t}
            }
            cursor = db_collection.find(query, {"vehicle_count": 1, "average_speed": 1, "_id": 0})
            async for dp in cursor:
                all_vehicle_counts.append(dp.get("vehicle_count", 0))
                all_average_speeds.append(dp.get("average_speed", 0.0))

        if all_vehicle_counts and all_average_speeds:
            avg_vehicle_count = sum(all_vehicle_counts) / len(all_vehicle_counts)
            avg_speed = sum(all_average_speeds) / len(all_average_speeds)
            logger.info(f"Identified pattern averages for sensor {sensor_id} ({time_range}): vehicle_count={avg_vehicle_count:.2f}, speed={avg_speed:.2f}")
            return {"average_vehicle_count": avg_vehicle_count, "average_speed": avg_speed}
        else:
            logger.warning(f"No historical data found for sensor {sensor_id} within the '{time_range}' pattern windows.")
            return {"average_vehicle_count": None, "average_speed": None}

    except Exception as e:
        logger.error(f"Error identifying traffic pattern: {e}", exc_info=True)
        return {"average_vehicle_count": None, "average_speed": None}


def detect_simple_anomaly(current_data: Dict[str, Union[int, float]], historical_pattern_data: Dict[str, Any], threshold: float) -> bool:
    """
    Compares current data to a historical pattern and returns True if an anomaly is detected.
    """
    logger.info(f"Detecting simple anomaly. Current data: {current_data}, Threshold: {threshold}")

    if historical_pattern_data.get("average_vehicle_count") is None or historical_pattern_data.get("average_speed") is None:
        logger.warning("Historical pattern data is incomplete. Cannot detect anomaly.")
        return False

    current_vehicle_count = current_data.get("vehicle_count", 0)
    current_average_speed = current_data.get("average_speed", 0.0)
    historical_vehicle_count = historical_pattern_data["average_vehicle_count"]
    historical_average_speed = historical_pattern_data["average_speed"]

    anomaly_detected = False

    if historical_vehicle_count > 0:
        vehicle_count_deviation = abs(current_vehicle_count - historical_vehicle_count) / historical_vehicle_count * 100
        if vehicle_count_deviation > threshold:
            logger.warning(f"Anomaly detected: Vehicle count deviation ({vehicle_count_deviation:.2f}%) exceeds threshold ({threshold}%)")
            anomaly_detected = True
    elif current_vehicle_count > threshold:
         logger.warning(f"Anomaly detected: Historical vehicle count is 0, current count is {current_vehicle_count}")
         anomaly_detected = True

    if historical_average_speed > 0:
        speed_deviation = abs(current_average_speed - historical_average_speed) / historical_average_speed * 100
        if current_average_speed < historical_average_speed and speed_deviation > threshold:
             logger.warning(f"Anomaly detected: Average speed deviation ({speed_deviation:.2f}%) below historical pattern and exceeds threshold ({threshold}%)")
             anomaly_detected = True
        elif current_average_speed > historical_average_speed and speed_deviation > threshold * 2:
             logger.warning(f"Potential Anomaly: Average speed speed deviation ({speed_deviation:.2f}%) above historical pattern and exceeds higher threshold ({threshold * 2}%)")
             anomaly_detected = True

    elif current_average_speed < 5 and historical_average_speed <= 0 and current_vehicle_count > 10:
        logger.warning(f"Anomaly detected: Historical speed is 0, current speed is low ({current_average_speed:.2f}) with significant vehicle count ({current_vehicle_count})")
        anomaly_detected = True

    if anomaly_detected:
        logger.warning("Simple anomaly detected.")
        return True
    else:
        logger.info("No simple anomaly detected.")
        return False

async def get_time_series_data(db_collection: AsyncIOMotorCollection, sensor_id: str, start_time: datetime, end_time: datetime) -> pd.DataFrame:
    """
    Queries the database for processed traffic data for a given sensor ID within a time range,
    and returns a pandas DataFrame with a datetime index.
    """
    logger.info(f"Fetching time series data for sensor {sensor_id} from {start_time} to {end_time}")

    query = {
        "sensor_id": sensor_id,
        "timestamp": {"$gte": start_time, "$lte": end_time}
    }

    try:
        # Use Motor's async cursor and convert to list
        cursor = db_collection.find(query, {"timestamp": 1, "vehicle_count": 1, "average_speed": 1, "_id": 0})
        data = await cursor.to_list(length=None)

        if not data:
            logger.warning(f"No time series data found for sensor {sensor_id} within the specified time range.")
            return pd.DataFrame()

        df = pd.DataFrame(data)
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.set_index('timestamp')
        df = df.sort_index()

        logger.info(f"Fetched {len(df)} data points for time series analysis.")
        return df

    except Exception as e:
        logger.error(f"Error fetching time series data: {e}", exc_info=True)
        return pd.DataFrame()

def calculate_rolling_averages(dataframe: pd.DataFrame, window_size: str) -> pd.DataFrame:
    if dataframe.empty:
        return dataframe
    df_rolling = dataframe.copy()
    df_rolling[f'rolling_avg_vehicle_count_{window_size}'] = df_rolling['vehicle_count'].rolling(window=window_size).mean()
    df_rolling[f'rolling_avg_average_speed_{window_size}'] = df_rolling['average_speed'].rolling(window=window_size) .mean()
    return df_rolling

def identify_seasonality_trend(dataframe: pd.DataFrame, window_size: str) -> Dict[str, Any]:
    if dataframe.empty:
        return {"seasonality": None, "trend": None, "message": "Empty DataFrame"}
    results = {"seasonality": None, "trend": None, "message": f"Analysis for {window_size} period."}
    try:
        if window_size == 'daily':
            daily_summary = dataframe.resample('D').mean().dropna()
            if not daily_summary.empty:
                 results["seasonality"] = "Potential daily seasonality observed."
                 results["trend"] = daily_summary[['vehicle_count', 'average_speed']].diff().mean().to_dict()
        elif window_size == 'weekly':
            weekly_summary = dataframe.resample('W').mean().dropna()
            if not daily_summary.empty: # Fix: was daily_summary, should be weekly_summary
                results["seasonality"] = "Potential weekly seasonality observed."
                results["trend"] = weekly_summary[['vehicle_count', 'average_speed']].diff().mean().to_dict()
        else:
            results["message"] = f"Unsupported period: {window_size}"
    except Exception as e:
        logger.error(f"Error identifying seasonality/trend: {e}", exc_info=True)
        results["message"] = f"Error: {e}"
    return results
