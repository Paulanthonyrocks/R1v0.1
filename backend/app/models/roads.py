from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class RoadPoint(BaseModel):
    lat: float = Field(ge=-90.0, le=90.0)
    lng: float = Field(ge=-180.0, le=180.0)


class Road(BaseModel):
    """A road/corridor in the warden's registry: a named polyline on the map.

    Feeds attach to a road via FeedConfigInfo.road_id; intersection node ids
    ("lat,lng", same format as the node-congestion registry) tie the road graph
    to junctions and signals.
    """
    id: str
    name: str = Field(min_length=1, max_length=200)
    polyline: List[RoadPoint] = Field(min_length=2)
    speed_limit_kmh: Optional[float] = Field(None, gt=0.0, le=400.0)
    # Compass bearing (deg) of permitted travel when the road is one-way;
    # None = bidirectional.
    one_way_bearing_deg: Optional[float] = Field(None, ge=0.0, le=360.0)
    intersection_ids: List[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class RoadCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    polyline: List[RoadPoint] = Field(min_length=2)
    speed_limit_kmh: Optional[float] = Field(None, gt=0.0, le=400.0)
    one_way_bearing_deg: Optional[float] = Field(None, ge=0.0, le=360.0)
    intersection_ids: List[str] = Field(default_factory=list)


class RoadUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    polyline: Optional[List[RoadPoint]] = Field(None, min_length=2)
    speed_limit_kmh: Optional[float] = Field(None, gt=0.0, le=400.0)
    one_way_bearing_deg: Optional[float] = Field(None, ge=0.0, le=360.0)
    intersection_ids: Optional[List[str]] = None


class SnapRequest(BaseModel):
    """Snap a map point (e.g. a camera position) to the nearest road."""
    latitude: float = Field(ge=-90.0, le=90.0)
    longitude: float = Field(ge=-180.0, le=180.0)
    max_distance_m: float = Field(150.0, gt=0.0, le=50000.0)


class RoadSnap(BaseModel):
    found: bool
    road_id: Optional[str] = None
    road_name: Optional[str] = None
    point: Optional[RoadPoint] = None
    distance_m: Optional[float] = None
    # Arc length from the road's polyline start to the snapped point (m).
    distance_along_m: Optional[float] = None
    # Local direction of travel of the segment snapped to (deg).
    segment_bearing_deg: Optional[float] = None
