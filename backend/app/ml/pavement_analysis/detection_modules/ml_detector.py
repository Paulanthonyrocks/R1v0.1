import logging
from pathlib import Path
import torch
import torchvision
import numpy as np
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

MODEL_PATH = Path(__file__).parent.parent.parent.parent.parent / "models" / "pavement"
CLASSES = ['longitudinal_crack', 'transverse_crack', 'alligator_crack', 'pothole', 'rutting']

def load_ml_model(model_path: str = None) -> torch.nn.Module:
    """
    Load the ML model for pavement distress detection
    Args:
        model_path: Optional path to model weights. If None, uses default path
    Returns:
        Loaded PyTorch model
    """
    global model
    try:
        if model_path is None:
            model_path = "./models/pavement/pavement_model.pt"
            
        logger.info(f"Loading ML model from {model_path}")
        model = torch.load(model_path)
        model.eval()
        return model
    except Exception as e:
        logger.error(f"Error loading ML model: {str(e)}")
        raise

def detect_distresses_ml(image: np.ndarray, model: torch.nn.Module) -> List[Dict[str, Any]]:
    """
    Detect pavement distresses using ML model
    Args:
        image: Input image as numpy array
        model: Loaded ML model
    Returns:
        List of detected distresses with their locations and confidence scores
    """
    try:
        # Convert image to tensor and normalize
        transform = torchvision.transforms.Compose([
            torchvision.transforms.ToTensor(),
            torchvision.transforms.Normalize(
                mean=[0.485, 0.456, 0.406],
                std=[0.229, 0.224, 0.225]
            )
        ])
        
        input_tensor = transform(image).unsqueeze(0)
        
        with torch.no_grad():
            predictions = model(input_tensor)
            
        # Process predictions into standard format
        detections = []
        for pred in predictions[0]:
            bbox = pred[:4].tolist()
            conf = float(pred[4])
            class_idx = int(pred[5])
            
            if conf > 0.5:  # Confidence threshold
                detection = {
                    'type': CLASSES[class_idx],
                    'bbox': {
                        'x1': bbox[0],
                        'y1': bbox[1],
                        'x2': bbox[2],
                        'y2': bbox[3]
                    },
                    'confidence': conf,
                    'measurements': {}  # To be filled by measurement modules
                }
                detections.append(detection)
                
        return detections
        
    except Exception as e:
        logger.error(f"Error during ML detection: {str(e)}")
        raise
