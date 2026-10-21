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
    # The wire field is `name` -- that is what the dashboard's AddFeedDialog
    # sends. It used to be named `name_hint` while routers/feeds.py read
    # `request.name`, so EVERY POST /api/v1/feeds/ raised AttributeError
    # (HTTP 500) on an otherwise valid payload. The feed manager's internal
    # `name_hint` kwarg is unrelated and unchanged.
    name: Optional[str] = None
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


class FeedOperationalStatusEnum(str, Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    STARTING = "starting"
    RESTARTING = "restarting"
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
    roi: Optional[List[Dict[str, float]]] = None
    exclusion_zones: Optional[List[List[Dict[str, float]]]] = None
    static_object_filter_enabled: Optional[bool] = None
    static_object_timeout: Optional[float] = None