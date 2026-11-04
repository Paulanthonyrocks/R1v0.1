from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class Preset(BaseModel):
    """A warden preset: a named group of cameras saved as a one-click layout.

    feed_ids are the camera group members; road_id optionally records the
    corridor the preset was built for (denormalized label only — membership
    is by feed_id, since a camera can be re-placed on another road).
    """
    id: str
    name: str = Field(min_length=1, max_length=200)
    feed_ids: List[str] = Field(min_length=1)
    road_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class PresetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    feed_ids: List[str] = Field(min_length=1)
    road_id: Optional[str] = None


class PresetUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    feed_ids: Optional[List[str]] = Field(None, min_length=1)
    road_id: Optional[str] = None
