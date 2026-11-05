from pydantic import BaseModel, ConfigDict
from typing import List, Optional
from enum import Enum


class DistressType(str, Enum):
    CRACK = "crack"
    POTHOLE = "pothole"
    RUTTING = "rutting"
    LONGITUDINAL_CRACK = "longitudinal_crack"
    TRANSVERSE_CRACK = "transverse_crack"
    ALLIGATOR_CRACK = "alligator_crack"
    ROAD_DAMAGE = "road_damage"
    UNKNOWN_DISTRESS = "unknown_distress"


class Measurement(BaseModel):
    # extra="allow": the analysis modules produce measurement keys like
    # length_mm / width_mm / area_sq_m / estimated_diameter_mm that must be
    # preserved verbatim, not dropped by pydantic's default extra="ignore".
    model_config = ConfigDict(extra="allow")

    length: Optional[float] = None
    width: Optional[float] = None
    depth: Optional[float] = None
    area: Optional[float] = None
    severity: str = "UNKNOWN"


class PavementDistress(BaseModel):
    type: DistressType
    location: dict
    measurements: Measurement
    confidence: float


class PavementAnalysisResponse(BaseModel):
    distresses: List[PavementDistress]
    pci_score: float
    image_url: Optional[str] = None
    report_url: Optional[str] = None
    plot_url: Optional[str] = None
