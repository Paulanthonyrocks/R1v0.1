import cv2
import numpy as np
import logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

def preprocess_image(
    image: np.ndarray,
    target_size: Optional[Tuple[int, int]] = None
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Preprocesses the input image for pavement analysis
    
    Args:
        image: Input image as numpy array
        target_size: Optional tuple of (width, height) for resizing
        
    Returns:
        Tuple of (preprocessed grayscale image, color image for visualization)
    """
    if image is None:
        logger.error("preprocess_image received a None image.")
        raise ValueError("Input image is None")

    # Copy for preservation
    image_for_dl_and_vis = image.copy()

    # Resize if needed
    if target_size and isinstance(target_size, tuple) and len(target_size) == 2:
        image_for_dl_and_vis = cv2.resize(image_for_dl_and_vis, target_size)
        image = cv2.resize(image, target_size)

    # Convert to grayscale for classical processing
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    # Apply denoising
    denoised = cv2.fastNlMeansDenoising(gray)

    # Enhance contrast
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
    enhanced = clahe.apply(denoised)

    return enhanced, image_for_dl_and_vis

def apply_roi(
    image: np.ndarray,
    roi_points: Optional[np.ndarray] = None
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Apply region of interest mask to the image
    
    Args:
        image: Input image
        roi_points: Optional numpy array of ROI polygon points
        
    Returns:
        Tuple of (masked image, mask)
    """
    if roi_points is None:
        # Default ROI is center 60% of image
        height, width = image.shape[:2]
        margin_x = int(width * 0.2)
        margin_y = int(height * 0.2)
        roi_points = np.array([
            [margin_x, margin_y],
            [width - margin_x, margin_y],
            [width - margin_x, height - margin_y],
            [margin_x, height - margin_y]
        ], dtype=np.int32)

    # Create mask
    mask = np.zeros(image.shape[:2], dtype=np.uint8)
    cv2.fillPoly(mask, [roi_points], 255)

    # Apply mask
    if len(image.shape) == 3:
        mask_3d = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        masked_image = cv2.bitwise_and(image, mask_3d)
    else:
        masked_image = cv2.bitwise_and(image, mask)

    return masked_image, mask
