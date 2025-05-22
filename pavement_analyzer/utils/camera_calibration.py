# utils/camera_calibration.py
import cv2
import numpy as np
import logging

logger = logging.getLogger(__name__)

# These will be populated by load_calibration_params
CAMERA_MATRIX = None
DIST_COEFFS = None
R_VECS = None # Rotation vectors
T_VECS = None # Translation vectors
SQUARE_SIZE_MM_FROM_CALIB = None # For reference, if needed for advanced pixel-to-mm
CALIBRATION_PARAMS_LOADED = False

# Fallback if calibration is not used or fails
DEFAULT_PIXEL_TO_MM_X = 0.5 # Example: 1 pixel = 0.5 mm horizontally
DEFAULT_PIXEL_TO_MM_Y = 0.5 # Example: 1 pixel = 0.5 mm vertically

def load_calibration_params(calib_file='calibration_params.npz'):
    global CAMERA_MATRIX, DIST_COEFFS, R_VECS, T_VECS, SQUARE_SIZE_MM_FROM_CALIB, CALIBRATION_PARAMS_LOADED
    try:
        with np.load(calib_file) as data:
            CAMERA_MATRIX = data['mtx']
            DIST_COEFFS = data['dist']
            R_VECS = data.get('rvecs') # .get() in case older file doesn't have it
            T_VECS = data.get('tvecs')
            SQUARE_SIZE_MM_FROM_CALIB = data.get('square_size_mm')
            CALIBRATION_PARAMS_LOADED = True
            logger.info(f"Calibration parameters loaded successfully from {calib_file}.")
            return CAMERA_MATRIX, DIST_COEFFS
    except FileNotFoundError:
        logger.warning(f"Calibration file '{calib_file}' not found. Undistortion will not be applied.")
        CALIBRATION_PARAMS_LOADED = False
        return None, None
    except Exception as e:
        logger.error(f"Error loading calibration parameters from {calib_file}: {e}")
        CALIBRATION_PARAMS_LOADED = False
        return None, None

def undistort_image(image, mtx, dist):
    if not CALIBRATION_PARAMS_LOADED or mtx is None or dist is None:
        return image.copy() # Return a copy of the original if no calibration

    h, w = image.shape[:2]
    new_camera_matrix, roi = cv2.getOptimalNewCameraMatrix(mtx, dist, (w, h), alpha=1, newImgSize=(w, h))
    
    dst = cv2.undistort(image, mtx, dist, None, new_camera_matrix)
    
    x, y, w_roi, h_roi = roi
    if w_roi > 0 and h_roi > 0 and x >=0 and y >=0 and (x+w_roi) <= w and (y+h_roi) <= h:
        return dst[y:y+h_roi, x:x+w_roi]
    return dst # Return full undistorted if ROI is not ideal for cropping

def get_pixel_to_mm_ratio(point_in_image=None, image_height=None):
    """
    Estimates pixel to mm conversion factor.
    This is a placeholder and highly dependent on setup and requires advanced techniques
    for high accuracy (e.g., knowing road plane, using rvecs/tvecs).
    A simple distance-based scaling can be a first approximation IF camera angle is fixed.
    """
    if CALIBRATION_PARAMS_LOADED and SQUARE_SIZE_MM_FROM_CALIB is not None and False: # Disabled for now
        # TODO: Implement advanced logic using rvecs, tvecs, and known road plane
        # This would involve projecting points from world (mm) to image (pixels)
        # and deriving the ratio locally. This is non-trivial.
        pass

    # Fallback to default or very simple perspective scaling
    # Example: if image_height and point_in_image are provided, scale based on y-coordinate
    # This assumes objects further down (higher y) are closer if camera looks down.
    # This is very empirical and needs tuning for a specific camera setup.
    scale_factor = 1.0
    if image_height and point_in_image:
        # Assume bottom of image (max y) is closest, top (0 y) is furthest
        # This factor makes pixels represent larger mm further away (smaller objects)
        # And smaller mm closer up (larger objects)
        # This logic is tricky and needs to be correct for the camera orientation
        # For a typical downward-looking camera:
        # scale_factor = 1.0 + ( (image_height - point_in_image[1]) / image_height ) * 0.5 # Example: vary by 50%
        pass # Keep scale_factor 1.0 for now to use defaults

    return DEFAULT_PIXEL_TO_MM_X * scale_factor, DEFAULT_PIXEL_TO_MM_Y * scale_factor