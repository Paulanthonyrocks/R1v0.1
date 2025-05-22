# detection_modules/classical_detector.py
import cv2
import numpy as np
import logging

logger = logging.getLogger(__name__)

def detect_cracks_classical(preprocessed_grayscale_image):
    if preprocessed_grayscale_image is None:
        logger.warning("detect_cracks_classical received None image.")
        return []
    try:
        thresh = cv2.adaptiveThreshold(preprocessed_grayscale_image, 255,
                                       cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY_INV,
                                       blockSize=21, C=5) # Tune blockSize and C

        kernel_ellipse = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)) # Smaller kernel
        closed = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, kernel_ellipse, iterations=1)
        # opened = cv2.morphologyEx(closed, cv2.MORPH_OPEN, kernel_ellipse, iterations=1) # Optional opening

        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        min_crack_area = 30  # Min pixel area
        potential_cracks = [c for c in contours if cv2.contourArea(c) > min_crack_area]
        return potential_cracks
    except Exception as e:
        logger.error(f"Error in classical crack detection: {e}")
        return []

def detect_potholes_classical(preprocessed_grayscale_image):
    if preprocessed_grayscale_image is None:
        logger.warning("detect_potholes_classical received None image.")
        return []
    try:
        # Potholes are often darker and larger.
        # Consider a different adaptive threshold strategy or even simple thresholding if contrast is good.
        # For adaptive, a larger block size might be better for potholes.
        thresh = cv2.adaptiveThreshold(preprocessed_grayscale_image, 255,
                                       cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY_INV,
                                       blockSize=55, C=8) # Tune blockSize and C

        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7,7))
        opened = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel, iterations=2) # Remove noise
        closed = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel, iterations=1) # Fill gaps in potholes

        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        potential_potholes = []
        min_pothole_area = 150
        min_circularity = 0.4
        for contour in contours:
            area = cv2.contourArea(contour)
            perimeter = cv2.arcLength(contour, True)
            if area > min_pothole_area and perimeter > 0:
                circularity = 4 * np.pi * (area / (perimeter * perimeter))
                if circularity > min_circularity:
                    x,y,w,h = cv2.boundingRect(contour)
                    aspect_ratio = float(w)/h if h > 0 else 0
                    if 0.4 < aspect_ratio < 2.5: # More flexible aspect ratio
                        potential_potholes.append(contour)
        return potential_potholes
    except Exception as e:
        logger.error(f"Error in classical pothole detection: {e}")
        return []