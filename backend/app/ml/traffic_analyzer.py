# backend/app/ml/traffic_analyzer.py
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Union

from pymongo.collection import Collection
import pandas as pd

# Setup logging
logger = logging.getLogger(__name__)

def get_average_traffic_data(db_collection: Collection, sensor_ids: List[str], start_time: datetime, end_time: datetime) -> Dict[str, Optional[float]]:
    """
    Queries the database for processed traffic data within the specified parameters and
    calculates the average vehicle count and average speed.

    Args:
        db_collection: MongoDB collection containing processed traffic data.
        sensor_ids: A list of sensor IDs to query.
        start_time: The start time of the query window.
        end_time: The end time of the query window.

    Returns:
        A dictionary with 'average_vehicle_count' and 'average_speed', or None if no data found.
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
        result = list(db_collection.aggregate(pipeline))

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

def identify_traffic_pattern(db_collection: Collection, sensor_id: str, time_range: str) -> Dict[str, Optional[float]]:
    """
    Queries historical data for a sensor within similar time ranges and calculates
    the average vehicle count and speed for that pattern.

    Args:
        db_collection: MongoDB collection containing processed traffic data.
        sensor_id: The ID of the sensor.
        time_range: A string representing the time range pattern (e.g., 'rush_hour', 'midnight').
                    This requires a predefined mapping or logic to determine the actual
                    start and end times for historical queries based on the current time.
                    (Placeholder implementation assumes a simple mapping or external logic).

    Returns:
        A dictionary with the pattern's 'average_vehicle_count' and 'average_speed',
        or None if no historical data found for the pattern.
    """
    logger.info(f"Identifying traffic pattern '{time_range}' for sensor {sensor_id}")

    # --- Placeholder for determining historical time windows based on time_range ---
    # In a real implementation, this would involve more sophisticated logic,
    # potentially based on the current day and time, and historical data analysis
    # to define typical patterns.
    # For this example, we'll use a simplified approach assuming 'rush_hour' is
    # generally between 7-9 AM and 4-6 PM, and 'midnight' is 12-1 AM.
    now = datetime.now(timezone.utc)
    historical_windows = []

    if time_range == 'rush_hour':
        # Example: Look at the same time window for the past 5 weekdays
        for i in range(1, 6):
            past_day = now - timedelta(days=i)
            if past_day.weekday() < 5: # Only consider weekdays
                # Morning rush hour
                start_am = past_day.replace(hour=7, minute=0, second=0, microsecond=0)
                end_am = past_day.replace(hour=9, minute=0, second=0, microsecond=0)
                historical_windows.append((start_am, end_am))
                # Evening rush hour
                start_pm = past_day.replace(hour=16, minute=0, second=0, microsecond=0)
                end_pm = past_day.replace(hour=18, minute=0, second=0, microsecond=0)
                historical_windows.append((start_pm, end_pm))
    elif time_range == 'midnight':
         # Example: Look at the same time window for the past 7 days
        for i in range(1, 8):
            past_day = now - datetime.timedelta(days=i)
            start_midnight = past_day.replace(hour=0, minute=0, second=0, microsecond=0)
            end_midnight = past_day.replace(hour=1, minute=0, second=0, microsecond=0)
            historical_windows.append((start_midnight, end_midnight))
    else:
        logger.warning(f"Unknown time range pattern: {time_range}. Cannot identify pattern.")
        return {"average_vehicle_count": None, "average_speed": None}
    # --- End of Placeholder ---

    all_vehicle_counts = []
    all_average_speeds = []

    try:
        for start_t, end_t in historical_windows:
            query = {
                "sensor_id": sensor_id,
                "timestamp": {"$gte": start_t, "$lte": end_t}
            }
            # Fetch data points for each historical window
            data_points = list(db_collection.find(query, {"vehicle_count": 1, "average_speed": 1, "_id": 0}))
            for dp in data_points:
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

    Args:
        current_data: Dictionary with 'vehicle_count' and 'average_speed'.
        historical_pattern_data: Dictionary with 'average_vehicle_count' and 'average_speed'.
        threshold: The percentage deviation threshold for anomaly detection.

    Returns:
        True if an anomaly is detected, False otherwise.
    """
    logger.info(f"Detecting simple anomaly. Current data: {current_data}, Threshold: {threshold}")

    if historical_pattern_data.get("average_vehicle_count") is None or historical_pattern_data.get("average_speed") is None:
        logger.warning("Historical pattern data is incomplete. Cannot detect anomaly.")
        return False

    current_vehicle_count = current_data.get("vehicle_count", 0)
    current_average_speed = current_data.get("average_speed", 0.0)
    historical_vehicle_count = historical_pattern_data["average_vehicle_count"]
    historical_average_speed = historical_pattern_data["average_speed"]

    # Simple anomaly detection logic:
    # Check for significant deviation in vehicle count or speed.
    # Avoid division by zero if historical averages are zero.

    anomaly_detected = False

    if historical_vehicle_count > 0:
        vehicle_count_deviation = abs(current_vehicle_count - historical_vehicle_count) / historical_vehicle_count * 100
        if vehicle_count_deviation > threshold:
            logger.warning(f"Anomaly detected: Vehicle count deviation ({vehicle_count_deviation:.2f}%) exceeds threshold ({threshold}%)")
            anomaly_detected = True
    elif current_vehicle_count > threshold: # If historical count is 0, any significant count is an anomaly
         logger.warning(f"Anomaly detected: Historical vehicle count is 0, current count is {current_vehicle_count}")
         anomaly_detected = True


    if historical_average_speed > 0:
        speed_deviation = abs(current_average_speed - historical_average_speed) / historical_average_speed * 100
        # Consider a drop in speed as an anomaly
        if current_average_speed < historical_average_speed and speed_deviation > threshold:
             logger.warning(f"Anomaly detected: Average speed deviation ({speed_deviation:.2f}%) below historical pattern and exceeds threshold ({threshold}%)")
             anomaly_detected = True
        # Consider a significant increase in speed if it indicates unusual low traffic
        elif current_average_speed > historical_average_speed and speed_deviation > threshold * 2: # Higher threshold for speed increase
             logger.warning(f"Potential Anomaly: Average speed deviation ({speed_deviation:.2f}%) above historical pattern and exceeds higher threshold ({threshold * 2}%)")
             anomaly_detected = True

    elif current_average_speed < 5 and historical_average_speed <= 0 and current_vehicle_count > 10: # If historical speed is 0 and current speed is low with vehicles
        logger.warning(f"Anomaly detected: Historical speed is 0, current speed is low ({current_average_speed:.2f}) with significant vehicle count ({current_vehicle_count})")
        anomaly_detected = True


    if anomaly_detected:
        logger.warning("Simple anomaly detected.")
        return True
    else:
        logger.info("No simple anomaly detected.")
        return False

def get_time_series_data(db_collection: Collection, sensor_id: str, start_time: datetime, end_time: datetime) -> pd.DataFrame:
    """
    Queries the database for processed traffic data for a given sensor ID within a time range,
    and returns a pandas DataFrame with a datetime index.

    Args:
        db_collection: MongoDB collection containing processed traffic data.
        sensor_id: The ID of the sensor.
        start_time: The start time of the query window.
        end_time: The end time of the query window.

    Returns:
        A pandas DataFrame with a datetime index and columns for 'vehicle_count' and 'average_speed',
        sorted by timestamp. Returns an empty DataFrame if no data is found or an error occurs.
    """
    logger.info(f"Fetching time series data for sensor {sensor_id} from {start_time} to {end_time}")

    query = {
        "sensor_id": sensor_id,
        "timestamp": {"$gte": start_time, "$lte": end_time}
    }

    try:
        # Fetch data and convert to list of dictionaries
        data = list(db_collection.find(query, {"timestamp": 1, "vehicle_count": 1, "average_speed": 1, "_id": 0}))

        if not data:
            logger.warning(f"No time series data found for sensor {sensor_id} within the specified time range.")
            return pd.DataFrame()

        # Convert to DataFrame
        df = pd.DataFrame(data)

        # Ensure timestamp is datetime and set as index
        df['timestamp'] = pd.to_datetime(df['timestamp'])
        df = df.set_index('timestamp')

        # Sort by index (timestamp)
        df = df.sort_index()

        logger.info(f"Fetched {len(df)} data points for time series analysis.")
        return df

    except Exception as e:
        logger.error(f"Error fetching time series data: {e}", exc_info=True)
        return pd.DataFrame()

def calculate_rolling_averages(dataframe: pd.DataFrame, window_size: str) -> pd.DataFrame:
    """
    Calculates rolling averages for 'vehicle_count' and 'average_speed'.

    Args:
        dataframe: pandas DataFrame with a datetime index and 'vehicle_count', 'average_speed' columns.
        window_size: The size of the rolling window (e.g., '5min', '1H', '1D').

    Returns:
        DataFrame with added columns for rolling averages.
    """
    if dataframe.empty:
        logger.warning("Input DataFrame is empty. Cannot calculate rolling averages.")
        return dataframe

    logger.info(f"Calculating rolling averages with window size: {window_size}")

    df_rolling = dataframe.copy()
    df_rolling[f'rolling_avg_vehicle_count_{window_size}'] = df_rolling['vehicle_count'].rolling(window=window_size).mean()
    df_rolling[f'rolling_avg_average_speed_{window_size}'] = df_rolling['average_speed'].rolling(window=window_size).mean()

    return df_rolling

def identify_seasonality_trend(dataframe: pd.DataFrame, period: str) -> Dict[str, Any]:
    """
    Attempts to identify seasonality and trend using simple resampling and aggregation.

    Args:
        dataframe: pandas DataFrame with a datetime index and traffic data.
        period: The period to check for seasonality ('daily', 'weekly').

    Returns:
        A dictionary containing information about the identified seasonality and trend.
    """
    if dataframe.empty:
        logger.warning("Input DataFrame is empty. Cannot identify seasonality/trend.")
        return {"seasonality": None, "trend": None, "message": "Empty DataFrame"}

    logger.info(f"Identifying seasonality and trend for period: {period}")

    results = {"seasonality": None, "trend": None, "message": f"Analysis for {period} period."}

    try:
        if period == 'daily':
            # Resample to daily frequency and calculate mean
            daily_summary = dataframe.resample('D').mean().dropna()
            if not daily_summary.empty:
                 results["seasonality"] = "Potential daily seasonality observed." # Placeholder
                 results["trend"] = daily_summary[['vehicle_count', 'average_speed']].diff().mean().to_dict() # Simple trend as daily change

        elif period == 'weekly':
            # Resample to weekly frequency and calculate mean
            weekly_summary = dataframe.resample('W').mean().dropna()
            if not weekly_summary.empty:
                results["seasonality"] = "Potential weekly seasonality observed." # Placeholder
                results["trend"] = weekly_summary[['vehicle_count', 'average_speed']].diff().mean().to_dict() # Simple trend as weekly change

        else:
            results["message"] = f"Unsupported period for seasonality/trend analysis: {period}"
            logger.warning(results["message"])

    except Exception as e:
        logger.error(f"Error identifying seasonality/trend: {e}", exc_info=True)
        results["message"] = f"Error during seasonality/trend analysis: {e}"

    return results

# Example Usage (requires a running MongoDB and data in the collection)
if __name__ == "__main__":
    # This is a placeholder for demonstration.
    # In a real application, you would get the MongoDB collection
    # from your application's database connection pool.
    from pymongo import MongoClient
    from datetime import timedelta

    # Ensure you have a MongoDB instance running and accessible
    # and data in the 'processed_traffic_data' collection of 'traffic_db_improved' database.
    MONGO_URI = "mongodb://localhost:27017/"
    MONGO_DB_NAME = "traffic_db_improved"
    PROCESSED_DATA_COLLECTION_NAME = "processed_traffic_data"
    
    try:
        client = MongoClient(MONGO_URI)
        db = client[MONGO_DB_NAME]
        processed_collection = db[PROCESSED_DATA_COLLECTION_NAME]
        logger.info("Connected to MongoDB for example usage.")

        # --- Example 1: Get average traffic data for a specific time range and sensors ---
        end_t = datetime.now(timezone.utc)
        start_t = end_t - timedelta(minutes=30)
        sensor_list = ["sensor_1", "sensor_2"]

        avg_data = get_average_traffic_data(processed_collection, sensor_list, start_t, end_t)
        print(f"\nAverage data for {sensor_list} between {start_t} and {end_t}: {avg_data}")

        # --- Example 2: Identify traffic pattern for a sensor ---
        sensor_id_pattern = "sensor_1"
        pattern_data_rush_hour = identify_traffic_pattern(processed_collection, sensor_id_pattern, "rush_hour")
        print(f"\nHistorical rush hour pattern data for {sensor_id_pattern}: {pattern_data_rush_hour}")

        pattern_data_midnight = identify_traffic_pattern(processed_collection, sensor_id_pattern, "midnight")
        print(f"\nHistorical midnight pattern data for {sensor_id_pattern}: {pattern_data_midnight}")


        # --- Example 3: Detect simple anomaly ---
        # Simulate some current data
        current_traffic = {"vehicle_count": 150, "average_speed": 10.5}
        # Use the identified pattern data (if successful)
        historical_pattern = pattern_data_rush_hour # or pattern_data_midnight
        anomaly_threshold = 30.0 # 30% deviation

        print(f"\nChecking for anomaly with current data: {current_traffic} against historical pattern: {historical_pattern}")
        is_anomaly = detect_simple_anomaly(current_traffic, historical_pattern, anomaly_threshold)
        print(f"Anomaly detected: {is_anomaly}")

        # --- Example 4: Get time series data and calculate rolling averages ---
        sensor_id_ts = "sensor_1"
        start_ts = datetime.now(timezone.utc) - timedelta(hours=24) # Last 24 hours
        end_ts = datetime.now(timezone.utc)

        ts_data = get_time_series_data(processed_collection, sensor_id_ts, start_ts, end_ts)
        print(f"\nTime series data for {sensor_id_ts}:\n{ts_data.head()}")

        if not ts_data.empty:
            rolling_avg_data = calculate_rolling_averages(ts_data, '15min')
            print(f"\nTime series data with rolling 15min averages:\n{rolling_avg_data.head()}")

            # --- Example 5: Identify seasonality and trend ---
            seasonality_trend_daily = identify_seasonality_trend(ts_data, 'daily')
            print(f"\nSeasonality and Trend analysis (daily): {seasonality_trend_daily}")

    except Exception as e:
        logger.error(f"An error occurred during example usage: {e}", exc_info=True)
    finally:
        if 'client' in locals() and client:
            client.close()
            logger.info("MongoDB connection closed.")