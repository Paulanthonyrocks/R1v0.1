# models/pavement_distress_model.py
import logging

logger = logging.getLogger(__name__)

# This file is intended to define data structures (like classes or dataclasses)
# that represent pavement distresses, measurements, and analysis results.
# This promotes type safety and better code organization.

# Example (using Python dataclasses for simplicity):
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class Measurements:
    area_pixels: Optional[float] = None
    perimeter_pixels: Optional[float] = None
    length_pixels: Optional[float] = None # For linear features like cracks
    width_pixels: Optional[float] = None # For linear features
    
    # Real-world measurements after calibration
    area_sq_mm: Optional[float] = None
    area_sq_m: Optional[float] = None
    length_mm: Optional[float] = None
    width_mm: Optional[float] = None
    
    # Pothole specific
    width_mm_bbox: Optional[float] = None
    height_mm_bbox: Optional[float] = None
    estimated_diameter_mm: Optional[float] = None

    # Rutting specific (simplified)
    bbox_width_mm: Optional[float] = None
    bbox_height_mm: Optional[float] = None
    # TODO: Add more specific rutting measures like max_depth_mm, profile_data

@dataclass
class PavementDistress:
    class_name: str # e.g., 'longitudinal_crack', 'pothole'
    detection_box: Optional[List[int]] = None # [x, y, w, h] in pixels
    score: Optional[float] = None # Confidence score from ML model
    measurements: Measurements = field(default_factory=Measurements)
    source: str = "Unknown" # 'classical', 'ml', 'manual'
    # TODO: Add severity level (Low, Medium, High)

@dataclass
class AnalysisResult:
    image_filename: str
    detected_distresses: List[PavementDistress] = field(default_factory=list)
    pci_score: Optional[float] = None
    # TODO: Add other overall analysis results, like total analyzed area, etc.

# Example Usage:
# distress = PavementDistress(class_name='pothole', detection_box=[10, 20, 50, 60], score=0.95)
# distress.measurements.area_sq_m = 0.25
# print(distress)

# results = AnalysisResult(image_filename='image1.jpg')
# results.detected_distresses.append(distress)
# results.pci_score = 85.5
# print(results)