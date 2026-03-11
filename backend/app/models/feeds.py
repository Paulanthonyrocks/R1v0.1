from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, Field
from enum import Enum


class FeedStatus(BaseModel):
    id: str
    source: str
    status: str = Field(..., examples=["stopped", "running", "starting", "error"])
    fps: Optional[float] = None
    error_message: Optional[str] = None


class FeedDetails(FeedStatus):
    name: Optional[str] = None
    last_update: Optional[datetime] = None
    last_capture: Optional[datetime] = None
    error_message: Optional[str] = None


class FeedCreateRequest(BaseModel):
    source: str = Field(..., examples=["/path/to/video.mp4", "webcam:0"])
    name_hint: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None


class FeedCreateResponse(BaseModel):
    feed_id: str
    status: str = "starting"
    message: str
    initial_status: Optional[str] = None


class StandardResponse(BaseModel):
    success: bool = True
    message: str


class FeedConfigInfo(BaseModel):
    name: str
    source_type: str
    source_identifier: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    roi: Optional[List[Dict[str, float]]] = None
    exclusion_zones: Optional[List[List[Dict[str, float]]]] = None
    static_object_filter_enabled: Optional[bool] = False
    static_object_timeout: Optional[float] = 300.0
    # UI Preferences
    show_bounding_boxes: Optional[bool] = True
    show_vehicle_details: Optional[bool] = True
    show_trajectories: Optional[bool] = True


class FeedOperationalStatusEnum(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    STARTING = "starting"
    ERROR = "error"


class FeedStatusData(BaseModel):
    feed_id: str
    config: FeedConfigInfo
    source: str
    status: FeedOperationalStatusEnum
    current_fps: Optional[float] = None
    last_error: Optional[str] = None
    latest_metrics: Optional[Dict[str, Any]] = None


class FeedStatusUpdate(BaseModel):
    feed_status_data: FeedStatusData


class FeedConfigRequest(BaseModel):
    name: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    roi: Optional[List[Dict[str, float]]] = None
    exclusion_zones: Optional[List[List[Dict[str, float]]]] = None
    static_object_filter_enabled: Optional[bool] = None
    static_object_timeout: Optional[float] = None
    show_bounding_boxes: Optional[bool] = None
    show_vehicle_details: Optional[bool] = None
    show_trajectories: Optional[bool] = None