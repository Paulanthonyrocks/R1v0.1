# backend/app/ml/traffic_analyzer.py
"""Pure-pandas traffic series helpers used by the /analyze/* endpoints.

The old MongoDB-collection functions (get_average_traffic_data,
identify_traffic_pattern, get_time_series_data, identify_seasonality_trend)
were removed: they took a Mongo-style collection that the active
DatabaseManager never implemented, and their only caller (routers/analysis.py)
now reads the persisted feed_metrics table via
DatabaseManager.get_feed_metrics_range instead.
"""
import logging
from typing import Any, Dict, Union

import pandas as pd

# Setup logging
logger = logging.getLogger(__name__)


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
