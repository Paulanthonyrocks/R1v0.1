# calibrate_camera.py
import cv2
import numpy as np
import glob
import os

# --- User Configuration ---
CHESSBOARD_SIZE = (7, 6) # Number of inner corners (width, height)
SQUARE_SIZE_MM = 25      # Actual size of a chessboard square in mm (IMPORTANT for real-world scale)
CALIBRATION_IMAGES_DIR = 'calibration_images/'
OUTPUT_CALIB_FILE = 'calibration_params.npz' # Output file in the main project directory
# --------------------------

# Termination criteria
criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 30, 0.001)

# Prepare object points, like (0,0,0), (1,0,0), (2,0,0) ....,(6,5,0)
objp = np.zeros((CHESSBOARD_SIZE[0] * CHESSBOARD_SIZE[1], 3), np.float32)
objp[:, :2] = np.mgrid[0:CHESSBOARD_SIZE[0], 0:CHESSBOARD_SIZE[1]].T.reshape(-1, 2)
objp = objp * SQUARE_SIZE_MM # Scale to real-world units (mm)

objpoints = []  # 3d point in real world space
imgpoints = []  # 2d points in image plane.

if not os.path.exists(CALIBRATION_IMAGES_DIR):
    print(f"Error: Directory '{CALIBRATION_IMAGES_DIR}' not found. Please create it and add chessboard images.")
    exit()

images = glob.glob(os.path.join(CALIBRATION_IMAGES_DIR, '*.jpg')) # Look for .jpg, .png etc.
images.extend(glob.glob(os.path.join(CALIBRATION_IMAGES_DIR, '*.png')))

if not images:
    print(f"No .jpg or .png images found in '{CALIBRATION_IMAGES_DIR}'.")
    exit()

print(f"Found {len(images)} images for calibration.")
img_shape = None # To store image shape for calibrateCamera

for fname in images:
    img = cv2.imread(fname)
    if img is None:
        print(f"Warning: Could not read image {fname}")
        continue

    if img_shape is None: # Get shape from the first valid image
        img_shape = img.shape[:2]

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    ret, corners = cv2.findChessboardCorners(gray, CHESSBOARD_SIZE, None)

    if ret == True:
        objpoints.append(objp)
        corners2 = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
        imgpoints.append(corners2)

        cv2.drawChessboardCorners(img, CHESSBOARD_SIZE, corners2, ret)
        # Resize for consistent display if images are large
        display_img = cv2.resize(img, (min(img.shape[1], 800), min(img.shape[0], 600)))
        cv2.imshow('Chessboard Detection', display_img)
        cv2.waitKey(500)
    else:
        print(f"Chessboard not detected in {fname}")

cv2.destroyAllWindows()

if not objpoints or not imgpoints or img_shape is None:
    print("Calibration failed: Not enough points found or image shape not determined.")
    exit()

print("Performing camera calibration...")
# Use gray.shape[::-1] or (img_shape[1], img_shape[0]) for image size
ret, mtx, dist, rvecs, tvecs = cv2.calibrateCamera(objpoints, imgpoints, (img_shape[1], img_shape[0]), None, None)

if ret:
    np.savez(OUTPUT_CALIB_FILE, mtx=mtx, dist=dist, rvecs=rvecs, tvecs=tvecs, square_size_mm=SQUARE_SIZE_MM)
    print(f"Calibration successful. Parameters saved to '{OUTPUT_CALIB_FILE}'")
    print("\nCamera Matrix (mtx):\n", mtx)
    print("\nDistortion Coefficients (dist):\n", dist)

    mean_error = 0
    for i in range(len(objpoints)):
        imgpoints2, _ = cv2.projectPoints(objpoints[i], rvecs[i], tvecs[i], mtx, dist)
        error = cv2.norm(imgpoints[i], imgpoints2, cv2.NORM_L2) / len(imgpoints2)
        mean_error += error
    print(f"\nTotal mean re-projection error: {mean_error / len(objpoints)}")
else:
    print("Calibration failed.")