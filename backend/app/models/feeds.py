# backend/app/models/feeds.py
from pydantic import BaseModel, ConfigDict
from typing import List

class StandardResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    message: str
    status_code: int = 200
    data: any = None # Or a more specific type if you know it

class FeedStatusData(BaseModel):
    feed_id: str
    status: str
    error_message: str | None = None # Using union type for str or None