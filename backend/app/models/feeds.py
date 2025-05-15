# backend/app/models/feeds.py
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
from datetime import datetime
import enum

class FeedOperationalStatusEnum(str, enum.Enum):
    RUNNING = "running"
    STOPPED = "stopped"
    STARTING = "starting"
    ERROR = "error"
    PENDING_DELETION = "pending_deletion"
    INITIALIZING = "initializing"
    DEGRADED = "degraded" # Running but with issues

class StandardResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    message: str
    status_code: int = 200
    data: Optional[Any] = None # Changed to Optional[Any]

class FeedConfigInfo(BaseModel):
    # Subset of the full feed config, for status display
    name: str = Field(..., example="Main Street Cam 1")
    source_type: str = Field(..., example="rtsp_stream") # e.g., rtsp_stream, video_file, sensor_api
    source_identifier: str = Field(..., example="rtsp://user:pass@192.168.1.100/stream1")
    # other key config details can be added if useful for status display

class FeedStatusData(BaseModel):
    feed_id: str = Field(..., example="feed_main_st_cam_1")
    config: FeedConfigInfo
    status: FeedOperationalStatusEnum = Field(..., example=FeedOperationalStatusEnum.RUNNING)
    status_message: Optional[str] = Field(None, example="Processing normally at 25 FPS")
    start_time: Optional[datetime] = Field(None, description="Timestamp when the feed was last started (UTC)")
    last_data_timestamp: Optional[datetime] = Field(None, description="Timestamp of the last data point processed from this feed (UTC)")
    processed_items_count: Optional[int] = Field(None, ge=0, example=150234, description="Total items processed since last start")
    items_per_second_current: Optional[float] = Field(None, ge=0, example=24.8, description="Current processing rate in items/second")
    error_details: Optional[str] = Field(None, description="Detailed error message if status is ERROR")
    latest_metrics: Optional[Dict[str, Any]] = Field(None, description="A selection of the most recent metrics from the feed")

class FeedCreateRequest(BaseModel):
    name: str = Field(..., example="North Intersection Camera")
    source_type: str = Field(..., example="video_file", description="Type of the feed source (e.g., 'video_file', 'rtsp_stream', 'webcam')")
    source: str = Field(..., example="/data/videos/traffic_footage_01.mp4", description="The actual source string (path, URL, device ID)")
    # Include other necessary configuration parameters for the feed
    # For example, specific detector model, processing resolution, etc.
    # These would depend on the 'source_type' and how FeedProcessor is designed.
    custom_config: Optional[Dict[str, Any]] = Field(None, description="Optional dictionary for custom feed-specific settings")

class FeedCreateResponse(BaseModel):
    feed_id: str
    message: str
    initial_status: FeedOperationalStatusEnum