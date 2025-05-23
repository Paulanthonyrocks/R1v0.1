from pydantic import BaseModel
from typing import List, Optional
from enum import Enum

class DistressType(str, Enum):
    CRACK = "crack"
    POTHOLE = "pothole"
    RUTTING = "rutting"

class Measurement(BaseModel):
    length: Optional[float]
    width: Optional[float]
    depth: Optional[float]
    area: Optional[float]
    severity: str

class PavementDistress(BaseModel):
    type: DistressType
    location: dict
    measurements: Measurement
    confidence: float

class PavementAnalysisResponse(BaseModel):
    distresses: List[PavementDistress]
    pci_score: float
    image_url: Optional[str]
    report_url: Optional[str]
