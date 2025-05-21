import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta
import numpy as np
from sklearn.preprocessing import StandardScaler
from sklearn.cluster import DBSCAN
from collections import defaultdict

from app.models.routing import (
    UserRoutingProfile,
    RouteHistoryEntry,
    TimeOfDay,
    RoadType,
    RoutePreferenceType
)

logger = logging.getLogger(__name__)

class UserPreferenceLearner:
    def __init__(self):
        self.scaler = StandardScaler()
        
    def _get_time_of_day(self, dt: datetime) -> TimeOfDay:
        """Convert datetime to TimeOfDay enum"""
        hour = dt.hour
        if 5 <= hour < 7:
            return TimeOfDay.EARLY_MORNING
        elif 7 <= hour < 9:
            return TimeOfDay.MORNING_RUSH
        elif 9 <= hour < 11:
            return TimeOfDay.MID_MORNING
        elif 11 <= hour < 14:
            return TimeOfDay.MIDDAY
        elif 14 <= hour < 16:
            return TimeOfDay.AFTERNOON
        elif 16 <= hour < 19:
            return TimeOfDay.EVENING_RUSH
        elif 19 <= hour < 21:
            return TimeOfDay.EVENING
        else:
            return TimeOfDay.NIGHT

    def _extract_route_features(self, history: List[RouteHistoryEntry]) -> np.ndarray:
        """Extract features from route history for clustering"""
        features = []
        for entry in history:
            # Convert time to cyclical features
            hour_sin = np.sin(2 * np.pi * entry.start_time.hour / 24)
            hour_cos = np.cos(2 * np.pi * entry.start_time.hour / 24)
            
            # Create feature vector
            feature = [
                hour_sin,
                hour_cos,
                entry.distance_km,
                entry.duration_minutes,
                len(entry.road_types_used),
                entry.user_rating if entry.user_rating else 3  # Default neutral rating
            ]
            features.append(feature)
        
        return np.array(features)

    def _identify_common_destinations(self, history: List[RouteHistoryEntry]) -> List[Dict[str, Any]]:
        """Identify frequently visited destinations using DBSCAN clustering"""
        if not history:
            return []

        # Extract end locations
        locations = np.array([[entry.end_location['latitude'], entry.end_location['longitude']] 
                            for entry in history])
        
        # Cluster locations
        clustering = DBSCAN(eps=0.01, min_samples=3).fit(locations)
        
        common_destinations = []
        unique_labels = set(clustering.labels_)
        
        for label in unique_labels:
            if label == -1:  # Skip noise points
                continue
                
            mask = clustering.labels_ == label
            cluster_points = locations[mask]
            cluster_entries = [entry for entry, is_in_cluster in zip(history, mask) if is_in_cluster]
            
            # Calculate cluster center
            center = cluster_points.mean(axis=0)
            
            # Analyze time patterns for this destination
            time_patterns = defaultdict(int)
            for entry in cluster_entries:
                time_of_day = self._get_time_of_day(entry.start_time)
                time_patterns[time_of_day] += 1
                
            common_destinations.append({
                'location': {
                    'latitude': float(center[0]),
                    'longitude': float(center[1])
                },
                'visit_count': len(cluster_entries),
                'time_patterns': dict(time_patterns),
                'average_duration': np.mean([entry.duration_minutes for entry in cluster_entries])
            })
            
        return sorted(common_destinations, key=lambda x: x['visit_count'], reverse=True)

    def _analyze_road_type_preferences(
        self,
        history: List[RouteHistoryEntry]
    ) -> Dict[TimeOfDay, Dict[RoadType, float]]:
        """Analyze user's road type preferences for different times of day"""
        preferences = {tod: {rt: 0.0 for rt in RoadType} for tod in TimeOfDay}
        counts = {tod: {rt: 0 for rt in RoadType} for tod in TimeOfDay}
        
        for entry in history:
            time_of_day = self._get_time_of_day(entry.start_time)
            rating = entry.user_rating if entry.user_rating is not None else 3
            
            for road_type in entry.road_types_used:
                preferences[time_of_day][road_type] += rating
                counts[time_of_day][road_type] += 1
        
        # Calculate average preferences
        for tod in TimeOfDay:
            for rt in RoadType:
                if counts[tod][rt] > 0:
                    preferences[tod][rt] = preferences[tod][rt] / counts[tod][rt]
                else:
                    preferences[tod][rt] = 3.0  # Neutral preference if no data
                    
        return preferences

    def _extract_time_patterns(
        self,
        history: List[RouteHistoryEntry]
    ) -> Dict[str, Dict[TimeOfDay, float]]:
        """Extract temporal patterns for common routes"""
        patterns = defaultdict(lambda: {tod: 0.0 for tod in TimeOfDay})
        route_counts = defaultdict(int)
        
        for entry in history:
            route_key = f"{entry.start_location}_{entry.end_location}"
            time_of_day = self._get_time_of_day(entry.start_time)
            patterns[route_key][time_of_day] += 1
            route_counts[route_key] += 1
        
        # Normalize frequencies
        for route_key in patterns:
            total = route_counts[route_key]
            patterns[route_key] = {
                tod: count / total 
                for tod, count in patterns[route_key].items()
            }
            
        return dict(patterns)

    def _calculate_feature_weights(
        self,
        history: List[RouteHistoryEntry]
    ) -> Dict[str, float]:
        """Calculate weights for different routing features based on user history"""
        if not history:
            return {
                'distance_weight': 0.3,
                'time_weight': 0.3,
                'traffic_weight': 0.2,
                'familiarity_weight': 0.1,
                'weather_weight': 0.1
            }
        
        # Initialize counters
        total_routes = len(history)
        traffic_sensitive = 0
        weather_sensitive = 0
        prefers_shorter = 0
        prefers_familiar = 0
        
        for entry in history:
            # Check if user avoids heavy traffic
            if entry.route_preference_used == RoutePreferenceType.LEAST_TRAFFIC:
                traffic_sensitive += 1
            
            # Check weather sensitivity
            if entry.weather_conditions and 'bad' in entry.weather_conditions.lower():
                weather_sensitive += 1
            
            # Check distance preference
            if entry.route_preference_used == RoutePreferenceType.SHORTEST:
                prefers_shorter += 1
            
            # Check familiarity preference (using common routes)
            if entry.route_id in [route.route_id for route in history[:-1]]:
                prefers_familiar += 1
        
        # Calculate weights
        weights = {
            'traffic_weight': traffic_sensitive / total_routes * 0.4,
            'weather_weight': weather_sensitive / total_routes * 0.2,
            'distance_weight': prefers_shorter / total_routes * 0.2,
            'familiarity_weight': prefers_familiar / total_routes * 0.1,
            'time_weight': 0.1  # Base weight for time
        }
        
        # Normalize weights to sum to 1
        total_weight = sum(weights.values())
        return {k: v/total_weight for k, v in weights.items()}

    def update_user_profile(
        self,
        user_id: str,
        history: List[RouteHistoryEntry],
        current_preferences: Optional[UserRoutingProfile] = None
    ) -> UserRoutingProfile:
        """Update user routing profile based on route history"""
        try:
            # Extract patterns and preferences
            common_destinations = self._identify_common_destinations(history)
            road_type_preferences = self._analyze_road_type_preferences(history)
            time_patterns = self._extract_time_patterns(history)
            routing_features = self._calculate_feature_weights(history)
            
            # Create or update profile
            profile = current_preferences or UserRoutingProfile(
                user_id=user_id,
                preferences=None,  # Will be set from existing or default
                time_patterns={},
                road_type_preferences={},
                common_destinations=[],
                routing_features={}
            )
            
            # Update profile with new data
            profile.common_destinations = common_destinations
            profile.road_type_preferences = road_type_preferences
            profile.time_patterns = time_patterns
            profile.routing_features = routing_features
            profile.last_updated = datetime.utcnow()
            
            return profile
            
        except Exception as e:
            logger.error(f"Error updating user profile for {user_id}: {e}")
            raise

    def get_route_recommendations(
        self,
        profile: UserRoutingProfile,
        start_location: Dict[str, float],
        end_location: Dict[str, float],
        departure_time: datetime
    ) -> Dict[str, Any]:
        """Get personalized routing recommendations based on user profile"""
        time_of_day = self._get_time_of_day(departure_time)
        
        # Check if this is a common route
        is_common_route = any(
            self._is_similar_location(dest['location'], end_location)
            for dest in profile.common_destinations
        )
        
        # Get preferred road types for this time
        preferred_roads = sorted(
            profile.road_type_preferences[time_of_day].items(),
            key=lambda x: x[1],
            reverse=True
        )
        
        # Build routing preferences
        recommendations = {
            'preferred_road_types': [rt for rt, _ in preferred_roads if _ >= 3.5],
            'avoid_road_types': [rt for rt, _ in preferred_roads if _ <= 2.5],
            'features_importance': profile.routing_features,
            'is_common_route': is_common_route,
            'time_of_day_factor': self._get_time_preference_score(
                profile, start_location, end_location, time_of_day
            )
        }
        
        return recommendations

    def _is_similar_location(
        self,
        loc1: Dict[str, float],
        loc2: Dict[str, float],
        threshold: float = 0.01
    ) -> bool:
        """Check if two locations are similar within a threshold"""
        return (
            abs(loc1['latitude'] - loc2['latitude']) < threshold and
            abs(loc1['longitude'] - loc2['longitude']) < threshold
        )

    def _get_time_preference_score(
        self,
        profile: UserRoutingProfile,
        start_location: Dict[str, float],
        end_location: Dict[str, float],
        time_of_day: TimeOfDay
    ) -> float:
        """Calculate time preference score for a route"""
        route_key = f"{start_location}_{end_location}"
        if route_key in profile.time_patterns:
            return profile.time_patterns[route_key].get(time_of_day, 0.0)
        return 0.0  # No historical data for this route
