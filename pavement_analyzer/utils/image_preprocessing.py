# utils/image_preprocessing.py
import cv2
import numpy as np
import logging

logger = logging.getLogger(__name__)

def preprocess_image(image, target_size=None):
    """
    Preprocesses the image: optional resize, grayscale, denoise, contrast enhancement.
    Returns:
        preprocessed_for_classical (numpy.ndarray): Grayscale, enhanced image for classical detectors.
        image_for_dl_and_vis (numpy.ndarray): Color image (possibly resized) for DL and visualization.
    """
    if image is None:
        logger.error("preprocess_image received a None image.")
        return None, None

    image_for_dl_and_vis = image.copy()

    if target_size and isinstance(target_size, tuple) and len(target_size) == 2:
        try:
            image_for_dl_and_vis = cv2.resize(image_for_dl_and_vis, target_size, interpolation=cv2.INTER_AREA)
        except Exception as e:
            logger.error(f"Error resizing image to {target_size}: {e}")
            # Continue with original size if resize fails

    # Convert to Grayscale
    try:
        gray = cv2.cvtColor(image_for_dl_and_vis, cv2.COLOR_BGR2GRAY)
    except Exception as e:
        logger.error(f"Error converting image to grayscale: {e}")
        # If grayscale conversion fails, classical methods might not work.
        # Return original color for DL and None for classical if this is critical.
        return None, image_for_dl_and_vis


    # Denoise (Gaussian Blur on grayscale)
    denoised_gray = cv2.GaussianBlur(gray, (5, 5), 0)

    # Contrast Enhancement (CLAHE on denoised grayscale)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    preprocessed_for_classical = clahe.apply(denoised_gray)
    
    return preprocessed_for_classical, image_for_dl_and_vis

def apply_roi(image, roi_polygon_points):
    """
    Applies a Region of Interest mask to the image.
    roi_polygon_points: list of (x,y) tuples defining the polygon.
    Returns the masked image.
    """
    if image is None or roi_polygon_points is None or len(roi_polygon_points) < 3:
        return image

    mask = np.zeros_like(image)
    if len(image.shape) > 2: # Color image
        channel_count = image.shape[2]
        ignore_mask_color = (255,) * channel_count
    else: # Grayscale image
        ignore_mask_color = 255
    
    try:
        pts = np.array(roi_polygon_points, dtype=np.int32)
        cv2.fillPoly(mask, [pts], ignore_mask_color)
        masked_image = cv2.bitwise_and(image, mask)
        return masked_image
    except Exception as e:
        logger.error(f"Error applying ROI: {e}")
        return image # Return original image if ROI application fails.