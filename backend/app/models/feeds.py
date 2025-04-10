# backend/app/models/feeds.py
from typing import Optional, List
from pydantic import BaseModel, Field

class FeedStatus(BaseModel):
    id: str
    source: str
    status: str = Field(..., examples=["stopped", "running", "starting", "error"])
    fps: Optional[float] = None
    error_message: Optional[str] = None

class FeedCreateRequest(BaseModel):
    source: str = Field(..., examples=["/path/to/video.mp4", "webcam:0"])
    name_hint: Optional[str] = None

class FeedCreateResponse(BaseModel):
    id: str
    status: str = "starting"
    message: str

class StandardResponse(BaseModel):
    success: bool = True
    message: str