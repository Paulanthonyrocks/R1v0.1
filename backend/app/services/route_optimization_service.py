import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from fastapi import HTTPException

from app.ml.route_optimizer import RouteOptimizer, OptimizedRoute
from app.models.traffic import LocationModel
from app.services.weather_service import WeatherService
from app.services.event_service import EventService

logger = logging.getLogger(__name__)

class RouteOptimizationService:
    def __init__(self, traffic_predictor, data_cache):
        self.optimizer = RouteOptimizer(traffic_predictor, data_cache)
        self.weather_service = WeatherService(data_cache)
        self.event_service = EventService(data_cache)
        logger.info("RouteOptimizationService initialized")

    async def get_optimized_route(self,
                                start_location: LocationModel,
                                end_location: LocationModel,
                                departure_time: Optional[datetime] = None,
                                preferences: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Get an optimized route with predictions and recommendations"""
        try:
            if departure_time is None:
                departure_time = datetime.now()

            # Get weather and event impacts
            weather_impact = await self.weather_service.get_weather_impact(
                start_location.latitude,
                start_location.longitude
            )
            
            event_impacts = await self.event_service.get_event_impacts()

            # Modify preferences based on weather/events
            adjusted_preferences = self._adjust_preferences_for_conditions(
                preferences or {}, 
                weather_impact,
                event_impacts
            )

            route = self.optimizer.optimize_route(
                start_lat=start_location.latitude,
                start_lon=start_location.longitude,
                end_lat=end_location.latitude,
                end_lon=end_location.longitude,
                departure_time=departure_time,
                consider_alternatives=adjusted_preferences.get('include_alternatives', True)
            )

            return self._format_route_response(
                route,
                adjusted_preferences,
                weather_impact=weather_impact,
                event_impacts=event_impacts
            )

        except Exception as e:
            logger.error(f"Error getting optimized route: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to optimize route: {str(e)}"
            )

    def _adjust_preferences_for_conditions(
        self,
        preferences: Dict[str, Any],
        weather_impact: Dict[str, Any],
        event_impacts: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Adjust routing preferences based on weather and event conditions"""
        adjusted = preferences.copy()

        # Adjust for severe weather
        if weather_impact['severity'] == 'High':
            adjusted['minimize_congestion'] = True
            adjusted['avoid_highways'] = True
            
        # Include alternative routes if there are high-impact events
        if any(impact['severity'] == 'High' for impact in event_impacts):
            adjusted['include_alternatives'] = True

        return adjusted

    def _format_route_response(self,
                             route: OptimizedRoute,
                             preferences: Dict[str, Any],
                             weather_impact: Optional[Dict[str, Any]] = None,
                             event_impacts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Format the route optimization response with impact data"""
        return {
            'primary_route': route.segments[0],
            'alternative_routes': [r.segments for r in route.alternative_routes],
            'route_metadata': {
                'total_distance': route.total_distance_km,
                'estimated_duration': route.estimated_duration_mins,
                'route_type': 'personalized',
                'optimization_factors': preferences
            },
            'weather_impact': weather_impact,
            'event_impacts': event_impacts,
            'confidence_score': self._calculate_confidence_score(
                route,
                weather_impact,
                event_impacts
            )
        }

    def _calculate_confidence_score(self,
                                 route: OptimizedRoute,
                                 weather_impact: Optional[Dict[str, Any]],
                                 event_impacts: Optional[List[Dict[str, Any]]]) -> float:
        """Calculate confidence score for the route based on conditions"""
        base_score = 0.9  # Start with 90% confidence
        
        # Reduce confidence for severe weather
        if weather_impact and weather_impact['severity'] == 'High':
            base_score *= 0.8
        elif weather_impact and weather_impact['severity'] == 'Medium':
            base_score *= 0.9
            
        # Reduce confidence for each high-severity event
        if event_impacts:
            high_severity_events = sum(1 for e in event_impacts if e['severity'] == 'High')
            base_score *= (0.9 ** high_severity_events)
            
        return round(base_score, 2)
