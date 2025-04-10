# backend/app/models/alerts.py
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field

class AlertItem(BaseModel):
    timestamp: datetime
    severity: str = Field(..., examples=["INFO", "WARNING", "ERROR", "CRITICAL"])
    feed_id: Optional[str] = None
    message: str

class AlertsResponse(BaseModel):
    alerts: List[AlertItem]
    total_count: int
    page: int
    limit: int