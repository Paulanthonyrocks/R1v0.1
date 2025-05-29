import logging
from pathlib import Path
import torch
from ultralytics import YOLO
import numpy as np
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).parent.parent.parent.parent.parent / "models" / "yolov8n.pt"
# Map YOLOv8 classes to our pavement distress types where possible
YOLO_TO_DISTRESS_MAP = {
    'crack': 'longitudinal_crack',  # Default to longitudinal for general cracks
    'pothole': 'pothole',
    'road damage': 'rutting'  # Map general road damage to rutting
}

def load_ml_model(model_path: str = None) -> YOLO:
    """
    Load the YOLOv8 model for object detection
    Args:
        model_path: Optional path to model weights. If None, uses default path
    Returns:
        Loaded YOLO model
    """
    global model
    try:
        if model_path is None:
            model_path = str(MODEL_PATH)
            
        logger.info(f"Loading YOLOv8 model from {model_path}")
        model = YOLO(model_path)
        return model
    except Exception as e:
        logger.error(f"Error loading YOLOv8 model: {str(e)}")
        raise

def detect_distresses_ml(image: np.ndarray, model: YOLO) -> List[Dict[str, Any]]:
    """
    Detect pavement distresses using YOLOv8 model
    Args:
        image: Input image as numpy array
        model: Loaded YOLO model
    Returns:
        List of detected distresses with their locations and confidence scores
    """
    try:
        # Run inference
        results = model(image, conf=0.25)  # Lower confidence threshold for testing
        
        detections = []
        for r in results[0]:  # Process first image's results
            bbox = r.boxes
            cls = int(bbox.cls[0])
            conf = float(bbox.conf[0])
            xyxy = bbox.xyxy[0].tolist()  # Get box coordinates in (x1, y1, x2, y2) format
            
            # Get class name and map it to our distress types
            class_name = model.names[cls].lower()
            distress_type = YOLO_TO_DISTRESS_MAP.get(class_name, 'unknown_distress')
            
            detection = {
                'type': distress_type,
                'bbox': {
                    'x1': xyxy[0],
                    'y1': xyxy[1],
                    'x2': xyxy[2],
                    'y2': xyxy[3]
                },
                'confidence': conf,
                'measurements': {}  # To be filled by measurement modules
            }
            detections.append(detection)
                
        return detections
        
    except Exception as e:
        logger.error(f"Error during YOLOv8 detection: {str(e)}")
        raise
