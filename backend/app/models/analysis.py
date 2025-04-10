# backend/app/models/analysis.py
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel

class TrendDataPoint(BaseModel):
    timestamp: datetime
    total_vehicles: Optional[int] = None
    avg_speed: Optional[float] = None
    congestion_index: Optional[float] = None
    speeding_vehicles: Optional[int] = None
    high_density_lanes: Optional[int] = None