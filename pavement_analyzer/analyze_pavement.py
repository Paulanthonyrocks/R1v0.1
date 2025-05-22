# analyze_pavement.py
import cv2
import numpy as np
import logging
import os

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Import modules
from .utils.camera_calibration import load_calibration_params, undistort_image, get_pixel_to_mm_ratio
from .utils.image_preprocessing import preprocess_image, apply_roi
from .utils.visualization import draw_dl_detections, display_pci_on_image, add_legend
from .detection_modules.classical_detector import detect_cracks_classical, detect_potholes_classical
from .detection_modules.ml_detector import load_ml_model, detect_distresses_ml
from .analysis_modules.crack_measurement import measure_crack
from .analysis_modules.pothole_measurement import measure_pothole
from .analysis_modules.rutting_analysis import analyze_rutting # Import the new module
from .analysis_modules.pci_calculator import calculate_pci
from .reporting.report_generator import generate_analysis_report
from .models.pavement_distress_model import PavementDistress, Measurements, AnalysisResult # Import data models

# --- Configuration ---
CALIBRATION_FILE = 'calibration_params.npz'
ML_MODEL_PATH = 'path/to/your/ml_model' # TODO: Update with actual model path
APPLY_ROI = False # Set to True to enable ROI masking
# Define ROI as a list of points [(x1, y1), (x2, y2), ...]
# Example: a simple rectangle ROI
# ROI_POLYGON_POINTS defined dynamically based on image size
# ----------------------

# Flags to enable/disable different detection methods
USE_CLASSICAL_CRACKS = True
USE_CLASSICAL_POTHOLES = True
USE_ML_DETECTOR = True
# ----------------------

def analyze_pavement_image(image_path):
    logging.info(f"Analyzing image: {image_path}")

    image = cv2.imread(image_path)
    if image is None:
        logging.error(f"Could not read image: {image_path}")
        return None
        
    img_height, img_width = image.shape[:2]
    # Update ROI points dynamically based on image size
    dynamic_roi_points = [
        (int(img_width*0.1), int(img_height*0.1)), 
        (int(img_width*0.9), int(img_height*0.1)),
        (int(img_width*0.9), int(img_height*0.9)),
        (int(img_width*0.1), int(img_height*0.9))
    ]

    # 1. Camera Calibration and Undistortion
    mtx, dist = load_calibration_params(CALIBRATION_FILE)
    undistorted_image = undistort_image(image, mtx, dist)
    logging.info("Image undistortion applied (if calibration params found).")
    
    # 2. Apply ROI Masking
    if APPLY_ROI and dynamic_roi_points:
         processed_image = apply_roi(undistorted_image, dynamic_roi_points)
         logging.info("ROI masking applied.")
    else:
         processed_image = undistorted_image
         logging.info("ROI masking skipped.")

    # 3. Image Preprocessing for Classical Methods
    preprocessed_for_classical, image_for_dl_and_vis = preprocess_image(processed_image)
    logging.info("Image preprocessing completed.")
    
    # 4. Distress Detection
    all_detections = [] # List to store all PavementDistress objects
    
    if USE_CLASSICAL_CRACKS and preprocessed_for_classical is not None:
        classical_crack_contours = detect_cracks_classical(preprocessed_for_classical)
        logging.info(f"Classical crack detection found {len(classical_crack_contours)} potential cracks.")
        for contour in classical_crack_contours:
             x,y,w,h = cv2.boundingRect(contour)
             measurements_data = measure_crack(contour, processed_image.shape)
             # Create PavementDistress object
             distress = PavementDistress(
                 class_name='unknown_crack', # Classical often doesn't classify crack types
                 detection_box=[x,y,w,h],
                 measurements=Measurements(**measurements_data), # Pass measurements dict
                 source='classical'
             )
             all_detections.append(distress)

    if USE_CLASSICAL_POTHOLES and preprocessed_for_classical is not None:
        classical_pothole_contours = detect_potholes_classical(preprocessed_for_classical)
        logging.info(f"Classical pothole detection found {len(classical_pothole_contours)} potential potholes.")
        for contour in classical_pothole_contours:
            x,y,w,h = cv2.boundingRect(contour)
            measurements_data = measure_pothole(contour, processed_image.shape)
            distress = PavementDistress(
                class_name='pothole',
                detection_box=[x,y,w,h],
                measurements=Measurements(**measurements_data),
                source='classical'
            )
            all_detections.append(distress)

    if USE_ML_DETECTOR and image_for_dl_and_vis is not None:
        ml_model = load_ml_model(ML_MODEL_PATH)
        if ml_model:
            ml_raw_detections = detect_distresses_ml(image_for_dl_and_vis, ml_model)
            logging.info(f"ML detection found {len(ml_raw_detections)} potential distresses.")
            for raw_det in ml_raw_detections:
                 # Assuming raw_det is a dict like {'box': [x,y,w,h], 'class_name': str, 'score': float}
                 class_name = raw_det.get('class_name', 'unknown_distress')
                 box = raw_det.get('box')
                 score = raw_det.get('score')
                 
                 # Placeholder: Call measurement functions based on class_name if needed
                 # For ML, the box and class are provided, need to potentially re-measure
                 # or refine based on contour from segmentation mask (if available from ML model)
                 measurements_data = {}
                 if box:
                     # Simple measurement from bounding box for ML detections
                     # More accurate measurement would require a mask or contour from the ML model
                     x,y,w,h = map(int, box)
                     # Use center of box for pixel-to-mm ratio estimation
                     center_x = x + w // 2
                     center_y = y + h // 2
                     point_in_image = (center_x, center_y)
                     px_to_mm_x, px_to_mm_y = get_pixel_to_mm_ratio(point_in_image=point_in_image, image_height=image.shape[0])

                     measurements_data = {
                         'area_pixels': w * h,
                         'length_pixels': max(w,h),
                         'width_pixels': min(w,h),
                         'bbox_width_mm': w * px_to_mm_x,
                         'bbox_height_mm': h * px_to_mm_y,
                         'area_sq_m': (w * h * px_to_mm_x * px_to_mm_y) / (1000*1000) # Estimate area in sq m
                     }
                     
                     # Call specific analysis modules if needed for more detail
                     if class_name == 'rutting' and box:
                          rutting_measures = analyze_rutting(processed_image, box)
                          measurements_data.update(rutting_measures) # Add rutting specific measures
                     # TODO: Add calls for alligator crack density, etc. if implemented


                 distress = PavementDistress(
                     class_name=class_name,
                     detection_box=box,
                     score=score,
                     measurements=Measurements(**measurements_data), # Pass measurements dict
                     source='ml'
                 )
                 all_detections.append(distress)

    # 5. Distress Analysis and Measurement (already done during detection for this structure)
    # This step would be more prominent if detection and measurement were separate processes.

    # Prepare data for PCI calculation (needs list of dicts with class_name and measurements)
    distress_data_for_pci = [{'class_name': d.class_name, 'measurements': d.measurements.__dict__} for d in all_detections]

    # 6. PCI Calculation
    pci_score = calculate_pci(distress_data_for_pci)
    logging.info(f"Calculated PCI: {pci_score}")

    # 7. Reporting and Visualization
    
    # Create an AnalysisResult object
    analysis_result = AnalysisResult(
        image_filename=os.path.basename(image_path),
        detected_distresses=all_detections,
        pci_score=pci_score
    )

    # Generate JSON report
    report_path = generate_analysis_report(
        image_filename=analysis_result.image_filename,
        distress_data=[d.__dict__ for d in analysis_result.detected_distresses], # Pass as dicts for JSON
        pci_score=analysis_result.pci_score
    )

    # Generate visualization image
    if image_for_dl_and_vis is not None:
        vis_image = image_for_dl_and_vis.copy()
        # Convert list of PavementDistress objects to list of dicts for drawing function
        detections_for_drawing = [d.__dict__ for d in analysis_result.detected_distresses]

        vis_image_with_detections = draw_dl_detections(vis_image, detections_for_drawing)
        vis_image_with_pci = display_pci_on_image(vis_image_with_detections, analysis_result.pci_score)
        vis_image_with_legend = add_legend(vis_image_with_pci) # Optional: add legend

        output_vis_path = os.path.join('reports/', f'{os.path.basename(image_path)}_visualization.jpg')
        cv2.imwrite(output_vis_path, vis_image_with_legend)
        logging.info(f"Visualization image saved: {output_vis_path}")

    logging.info(f"Analysis of {image_path} complete.")
    return analysis_result

# Example Usage:
# if __name__ == '__main__':
#     sample_image_path = 'sample_data/sample_pavement_image.jpg' # TODO: Add a sample image here
#     if os.path.exists(sample_image_path):
#          analysis_results = analyze_pavement_image(sample_image_path)
#          if analysis_results:
#              print("\nAnalysis Results Summary:")
#              print(f"Image: {analysis_results.image_filename}")
#              print(f"Calculated PCI: {analysis_results.pci_score:.2f}")
#              print(f"Detected Distresses: {len(analysis_results.detected_distresses)}")
#              # print("Detailed Detections:", analysis_results.detected_distresses) # Uncomment for detail
#     else:
#          print(f"Sample image not found at {sample_image_path}. Please add one to run the example.")

# To run the example, you would need to:
# 1. Place a sample image in the 'pavement_analyzer/sample_data/' directory.
# 2. (Optional) Run calibrate_camera.py with chessboard images to get calibration_params.npz
# 3. (Optional) Obtain and specify the path to an ML model in ML_MODEL_PATH
# 4. Uncomment the example usage block below (if running as a script)