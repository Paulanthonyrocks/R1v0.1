from typing import List, Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel, Field
from enum import Enum

class RoutePreferenceType(str, Enum):
    HIGHWAYS = "highways"
    SCENIC = "scenic"
    SHORTEST = "shortest"
    LEAST_TRAFFIC = "least_traffic"
    AVOID_TOLLS = "avoid_tolls"

class TimeOfDay(str, Enum):
    EARLY_MORNING = "early_morning"  # 5-7 AM
    MORNING_RUSH = "morning_rush"    # 7-9 AM
    MID_MORNING = "mid_morning"      # 9-11 AM
    MIDDAY = "midday"               # 11 AM-2 PM
    AFTERNOON = "afternoon"         # 2-4 PM
    EVENING_RUSH = "evening_rush"   # 4-7 PM
    EVENING = "evening"             # 7-9 PM
    NIGHT = "night"                 # 9 PM-5 AM

class RoadType(str, Enum):
    HIGHWAY = "highway"
    MAIN_ROAD = "main_road"
    LOCAL_STREET = "local_street"
    SCENIC_ROUTE = "scenic_route"
    TOLL_ROAD = "toll_road"

class UserRoutePreferences(BaseModel):
    """User's general routing preferences"""
    user_id: str
    default_preference: RoutePreferenceType = Field(default=RoutePreferenceType.LEAST_TRAFFIC)
    avoid_road_types: List[RoadType] = Field(default_factory=list)
    preferred_road_types: List[RoadType] = Field(default_factory=list)
    max_tolls_amount: Optional[float] = None
    preferred_departure_times: Dict[str, datetime] = Field(default_factory=dict)
    avoid_intersections: List[Dict[str, float]] = Field(default_factory=list)
    favorite_routes: List[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class RouteHistoryEntry(BaseModel):
    """Single route history entry"""
    user_id: str
    route_id: str
    start_location: Dict[str, float]
    end_location: Dict[str, float]
    start_time: datetime
    end_time: datetime
    route_preference_used: RoutePreferenceType
    road_types_used: List[RoadType]
    distance_km: float
    duration_minutes: float
    traffic_conditions: str
    weather_conditions: Optional[str]
    user_rating: Optional[int]
    feedback: Optional[str]

class UserRoutingProfile(BaseModel):
    """Combined user routing profile with preferences and learned behaviors"""
    user_id: str
    preferences: UserRoutePreferences
    time_patterns: Dict[str, Dict[TimeOfDay, float]]  # Route -> TimeOfDay -> Frequency
    road_type_preferences: Dict[TimeOfDay, Dict[RoadType, float]]  # TimeOfDay -> RoadType -> Preference Score
    common_destinations: List[Dict[str, Any]]
    routing_features: Dict[str, float]  # Learned feature weights
    last_updated: datetime = Field(default_factory=datetime.utcnow)

class PersonalizedRouteRequest(BaseModel):
    """Request for a personalized route"""
    user_id: str
    start_location: Dict[str, float]
    end_location: Dict[str, float]
    departure_time: datetime
    route_preference: Optional[RoutePreferenceType] = None
    consider_weather: bool = True
    consider_events: bool = True
    max_alternatives: int = Field(default=3, ge=1, le=5)

class PersonalizedRouteResponse(BaseModel):
    """Response containing personalized route options"""
    primary_route: Dict[str, Any]
    alternative_routes: List[Dict[str, Any]]
    route_metadata: Dict[str, Any]
    user_preferences_applied: List[str]
    weather_impact: Optional[Dict[str, Any]]
    event_impacts: List[Dict[str, Any]]
    confidence_score: float
