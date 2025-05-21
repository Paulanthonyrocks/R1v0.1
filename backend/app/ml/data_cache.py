import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import numpy as np
from collections import defaultdict

logger = logging.getLogger(__name__)

class TrafficDataCache:
    def __init__(self, max_history_hours: int = 24):
        self.max_history_hours = max_history_hours
        self.location_data: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        
    def _get_location_key(self, latitude: float, longitude: float) -> str:
        """Create a unique key for a location, rounding to 4 decimal places for nearby grouping"""
        return f"{round(latitude, 4)},{round(longitude, 4)}"
        
    def add_data_point(self, 
                      latitude: float, 
                      longitude: float, 
                      timestamp: datetime,
                      data: Dict[str, Any]):
        """Add a new data point for a location"""
        location_key = self._get_location_key(latitude, longitude)
        
        # Add new data point
        data_point = {
            'timestamp': timestamp,
            **data
        }
        self.location_data[location_key].append(data_point)
        
        # Clean old data
        self._clean_old_data(location_key)
        
    def _clean_old_data(self, location_key: str):
        """Remove data points older than max_history_hours"""
        if not self.location_data[location_key]:
            return
            
        cutoff_time = datetime.now() - timedelta(hours=self.max_history_hours)
        self.location_data[location_key] = [
            point for point in self.location_data[location_key]
            if point['timestamp'] > cutoff_time
        ]
        
    def get_recent_data(self, 
                       latitude: float, 
                       longitude: float, 
                       hours: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get recent data points for a location"""
        location_key = self._get_location_key(latitude, longitude)
        data = self.location_data.get(location_key, [])
        
        if not data:
            return []
            
        if hours is None:
            return data
            
        cutoff_time = datetime.now() - timedelta(hours=hours)
        return [point for point in data if point['timestamp'] > cutoff_time]
        
    def get_statistics(self, 
                      latitude: float, 
                      longitude: float, 
                      hours: Optional[int] = None) -> Dict[str, Any]:
        """Calculate statistics for a location's recent data"""
        data = self.get_recent_data(latitude, longitude, hours)
        
        if not data:
            return {
                'count': 0,
                'avg_vehicle_count': None,
                'avg_speed': None,
                'peak_vehicle_count': None,
                'min_speed': None
            }
            
        vehicle_counts = [d.get('vehicle_count', 0) for d in data if 'vehicle_count' in d]
        speeds = [d.get('average_speed', 0) for d in data if 'average_speed' in d]
        
        return {
            'count': len(data),
            'avg_vehicle_count': np.mean(vehicle_counts) if vehicle_counts else None,
            'avg_speed': np.mean(speeds) if speeds else None,
            'peak_vehicle_count': max(vehicle_counts) if vehicle_counts else None,
            'min_speed': min(speeds) if speeds else None,
            'congestion_frequency': self._calculate_congestion_frequency(data)
        }
        
    def _calculate_congestion_frequency(self, data: List[Dict[str, Any]]) -> float:
        """Calculate how often the location experiences congestion"""
        if not data:
            return 0.0
            
        congestion_count = sum(
            1 for d in data 
            if d.get('congestion_score', 0) > 70 or 
               (d.get('average_speed', 60) < 20 and d.get('vehicle_count', 0) > 30)
        )
        
        return congestion_count / len(data)
