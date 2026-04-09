import logging
import threading
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta, timezone
import numpy as np
from collections import defaultdict

logger = logging.getLogger("app.ml")


class TrafficDataCache:
    def __init__(self, max_history_hours: int = 24):
        self.max_history_hours = max_history_hours
        self.location_data: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._lock = threading.RLock()
        
        # Start background cleanup thread
        self._stop_cleanup = threading.Event()
        self._cleanup_thread = threading.Thread(target=self._bg_cleanup_loop, daemon=True)
        self._cleanup_thread.start()

    def _get_location_key(self, latitude: float, longitude: float) -> str:
        """Create a unique key for a location, rounding to 4 decimal places for nearby grouping"""
        return f"{round(latitude, 4)},{round(longitude, 4)}"

    def add_data_point(
        self,
        latitude: float,
        longitude: float,
        timestamp: datetime,
        data: Dict[str, Any],
    ):
        """Add a new data point for a location"""
        location_key = self._get_location_key(latitude, longitude)

        # Add new data point, ensuring we use the datetime timestamp argument 
        # and it's not overwritten by anything in the 'data' dict
        data_point = {**data, "timestamp": timestamp}
        
        with self._lock:
            logger.debug(f"Adding data point for {location_key}: {data_point}")
            self.location_data[location_key].append(data_point)

            logger.debug(
                f"Data point added for {location_key}. Current points: {len(self.location_data[location_key])}"
            )
            
            # Clean old data for this location to prevent memory leak
            self._clean_old_data(location_key)

    def _is_later_than(self, ts: Any, cutoff: datetime) -> bool:
        """Safely compare a timestamp (which could be float/str) with a cutoff datetime."""
        if not isinstance(ts, datetime):
            try:
                if isinstance(ts, (int, float)):
                    ts = datetime.fromtimestamp(ts, tz=timezone.utc)
                elif isinstance(ts, str):
                    ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                else:
                    return False
            except (ValueError, TypeError, OSError):
                return False
        
        # Ensure ts has timezone if cutoff has timezone
        if ts.tzinfo is None and cutoff.tzinfo is not None:
            ts = ts.replace(tzinfo=timezone.utc)
            
        return ts > cutoff

    def _clean_old_data(self, location_key: str):
        """Remove data points older than max_history_hours"""
        # Note: This is called within self._lock in add_data_point and clean_all_locations
        if not self.location_data[location_key]:
            return

        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=self.max_history_hours)
        self.location_data[location_key] = [
            point
            for point in self.location_data[location_key]
            if self._is_later_than(point.get("timestamp"), cutoff_time)
        ]

    def get_recent_data(
        self, latitude: float, longitude: float, hours: Optional[int] = None, num_points: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """Get recent data points for a location"""
        location_key = self._get_location_key(latitude, longitude)
        
        with self._lock:
            data = list(self.location_data.get(location_key, []))

        if not data:
            return []

        if hours is not None:
            cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
            data = [point for point in data if self._is_later_than(point.get("timestamp"), cutoff_time)]

        if num_points is not None:
            data = data[-num_points:]

        return data

    def get_statistics(
        self, latitude: float, longitude: float, hours: Optional[int] = None
    ) -> Dict[str, Any]:
        """Calculate statistics for a location's recent data"""
        data = self.get_recent_data(latitude, longitude, hours)

        if not data:
            return {
                "count": 0,
                "avg_vehicle_count": None,
                "avg_speed": None,
                "peak_vehicle_count": None,
                "min_speed": None,
            }

        vehicle_counts = [
            d.get("vehicle_count", 0) for d in data if "vehicle_count" in d
        ]
        speeds = [d.get("average_speed", 0) for d in data if "average_speed" in d]

        return {
            "count": len(data),
            "avg_vehicle_count": np.mean(vehicle_counts) if vehicle_counts else None,
            "avg_speed": np.mean(speeds) if speeds else None,
            "peak_vehicle_count": max(vehicle_counts) if vehicle_counts else None,
            "min_speed": min(speeds) if speeds else None,
            "congestion_frequency": self._calculate_congestion_frequency(data),
        }

    def _calculate_congestion_frequency(self, data: List[Dict[str, Any]]) -> float:
        """Calculate how often the location experiences congestion"""
        if not data:
            return 0.0

        congestion_count = sum(
            1
            for d in data
            if d.get("congestion_score", 0) > 70
            or (d.get("average_speed", 60) < 20 and d.get("vehicle_count", 0) > 30)
        )

        return congestion_count / len(data)

    def get_all_location_summaries(self) -> List[Dict[str, Any]]:
        """
        Retrieves the latest data summary for all tracked locations.
        A "summary" here means the most recent data point's key metrics.
        """
        logger.debug("Retrieving all location summaries.")
        summaries = []

        with self._lock:
            # We use list(self.location_data.items()) to avoid RuntimeError if the dict changes size
            for location_key, data_points in list(self.location_data.items()):
                if not data_points:
                    logger.debug(
                        f"No data points for location_key: {location_key}. Skipping."
                    )
                    continue

                # Assume the last data point is the most recent one
                latest_point = data_points[-1]

                try:
                    lat_str, lon_str = location_key.split(",")
                    latitude = float(lat_str)
                    longitude = float(lon_str)
                except ValueError:
                    logger.warning(
                        f"Could not parse location_key: {location_key}. Skipping this entry."
                    )
                    continue

                summary = {
                    **latest_point,
                    "id": location_key,
                    "name": f"Node at ({latitude:.4f}, {longitude:.4f})",
                    "latitude": latitude,
                    "longitude": longitude,
                }
                summaries.append(summary)
        
        logger.debug(f"Returning {len(summaries)} location summaries.")
        return summaries

    def clean_all_locations(self):
        """Iterate through all locations and clean old data."""
        logger.info("Starting cleanup of old data for all locations.")
        with self._lock:
            for location_key in list(self.location_data.keys()):
                self._clean_old_data(location_key)
        logger.info("Finished cleanup of old data for all locations.")

    def _bg_cleanup_loop(self):
        """Background loop to clean all locations periodically"""
        while not self._stop_cleanup.is_set():
            try:
                # Clean every hour
                self.clean_all_locations()
            except Exception as e:
                logger.error(f"Error in background cleanup loop: {e}")
            
            # Sleep for 1 hour or until stopped
            self._stop_cleanup.wait(timeout=3600)

    def shutdown(self):
        """Stop the background cleanup thread"""
        self._stop_cleanup.set()
        self._cleanup_thread.join()
