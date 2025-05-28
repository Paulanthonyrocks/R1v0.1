import cv2
import numpy as np
import logging
from pathlib import Path
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any, Optional
import os
from datetime import datetime

from .reporting.report_generator import generate_analysis_report
from ...models.pavement import PavementAnalysisResponse, PavementDistress, DistressType
from .utils.camera_calibration import load_calibration_params, undistort_image, get_pixel_to_mm_ratio
from .utils.image_preprocessing import preprocess_image
from .detection_modules.ml_detector import load_ml_model, detect_distresses_ml
from .analysis_modules import (
    calculate_pci,
    measure_crack_contour,
    measure_pothole_contour,
    analyze_rutting_bbox
)

# Initialize logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(module)s - %(message)s')
logger = logging.getLogger(__name__)

# Initialize thread pool for CPU-intensive operations
thread_pool = ThreadPoolExecutor(max_workers=os.cpu_count())

# --- Configuration ---
CALIBRATION_FILE_PATH = "./calibration_params.npz"
# -------------------

# Load ML model once at module level
ml_model = load_ml_model()

# Load camera calibration parameters once at module level
calibration_params: Optional[Dict[str, Any]] = None
if os.path.exists(CALIBRATION_FILE_PATH):
    try:
        calibration_params = load_calibration_params(CALIBRATION_FILE_PATH)
        logger.info(f"Successfully loaded camera calibration parameters from {CALIBRATION_FILE_PATH}")
    except Exception as e:
        logger.error(f"Failed to load camera calibration parameters from {CALIBRATION_FILE_PATH}: {e}", exc_info=True)
        calibration_params = None
else:
    logger.warning(f"Camera calibration file not found at {CALIBRATION_FILE_PATH}. Proceeding without specific calibration.")

async def _run_in_executor(func, *args):
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(thread_pool, func, *args)

async def analyze_pavement_image(image_data: bytes, image_filename: str = "uploaded_image.png") -> PavementAnalysisResponse:
    """
    Asynchronously analyze pavement image for distresses.
    Processes the image, detects distresses, measures them, calculates PCI, and generates a report.
    """
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        base_filename = f"{timestamp}_{Path(image_filename).stem}"

        logger.info(f"Starting pavement analysis for image: {image_filename} (Timestamp: {timestamp})")

        nparr = np.frombuffer(image_data, np.uint8)
        original_image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        if original_image is None:
            logger.error("Failed to decode image data.")
            raise ValueError("Invalid image data")

        img_height, img_width = original_image.shape[:2]
        logger.debug(f"Image dimensions: {img_width}x{img_height}")

        # Define storage paths
        config = {
            "image_output_dir": "./data/pavement_images/analyzed",
            "report_output_dir": "./data/pavement_reports"
        }
        image_output_dir = Path(config["image_output_dir"])
        report_output_dir = Path(config["report_output_dir"])
        image_output_dir.mkdir(parents=True, exist_ok=True)
        report_output_dir.mkdir(parents=True, exist_ok=True)

        # 1. Undistort Image (if calibration parameters are available)
        if calibration_params and 'camera_matrix' in calibration_params and 'dist_coeffs' in calibration_params:
            undistorted_image = await _run_in_executor(
                undistort_image, 
                original_image, 
                calibration_params['camera_matrix'], 
                calibration_params['dist_coeffs']
            )
            logger.info("Image undistortion applied.")
        else:
            undistorted_image = original_image.copy()
            logger.info("Image undistortion skipped (no calibration data).")

        # 2. Preprocess image (for ML model and visualization)
        processed_image_for_ml, _ = await _run_in_executor(
            preprocess_image,
            undistorted_image.copy()
        )

        # 3. Detect distresses using ML model
        if ml_model is None:
            logger.error("ML model is not loaded. Cannot perform detection.")
            raise RuntimeError("ML model not available for analysis.")
        
        ml_detections_raw = await _run_in_executor(
            detect_distresses_ml,
            undistorted_image,
            ml_model
        )
        logger.info(f"ML model detected {len(ml_detections_raw)} raw distresses.")

        # 4. Detailed Measurement and PavementDistress object creation
        processed_distresses: List[PavementDistress] = []
        for det_idx, raw_det in enumerate(ml_detections_raw):
            try:
                distress_type_str = raw_det.get('type', 'unknown_distress').lower()
                bbox = raw_det.get('bbox')
                score = raw_det.get('confidence', 0.0)
                segmentation_mask = raw_det.get('mask')

                if bbox is None:
                    logger.warning(f"Skipping detection {det_idx} due to missing bounding box.")
                    continue
                
                x1, y1, x2, y2 = bbox.get('x1'), bbox.get('y1'), bbox.get('x2'), bbox.get('y2')
                if any(v is None for v in [x1, y1, x2, y2]):
                    logger.warning(f"Skipping detection {det_idx} due to incomplete bounding box values.")
                    continue
                
                rect_box = [int(x1), int(y1), int(x2-x1), int(y2-y1)]

                measurements_data: Dict[str, float] = {}
                
                if 'crack' in distress_type_str:
                    if segmentation_mask is not None and segmentation_mask.size > 0:
                        contours, _ = cv2.findContours(segmentation_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                        if contours:
                            main_contour = max(contours, key=cv2.contourArea)
                            measurements_data = measure_crack_contour(main_contour, undistorted_image.shape[:2], calibration_params)
                        else:
                            logger.warning(f"Crack detection {det_idx} had a mask but no contours found.")
                    else:
                        logger.warning(f"Crack detection {det_idx} ({distress_type_str}) has no segmentation mask. Measurement will be basic.")
                        px_to_mm_x, px_to_mm_y = get_pixel_to_mm_ratio(calibration_params, image_height=img_height, point_in_image=(rect_box[0] + rect_box[2]//2, rect_box[1] + rect_box[3]//2))
                        measurements_data['length_mm'] = rect_box[3] * px_to_mm_y
                        measurements_data['width_mm'] = rect_box[2] * px_to_mm_x
                        measurements_data['area_sq_m'] = (rect_box[2] * px_to_mm_x * rect_box[3] * px_to_mm_y) / (1000*1000)

                elif distress_type_str == 'pothole':
                    if segmentation_mask is not None and segmentation_mask.size > 0:
                        contours, _ = cv2.findContours(segmentation_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                        if contours:
                            main_contour = max(contours, key=cv2.contourArea)
                            measurements_data = measure_pothole_contour(main_contour, undistorted_image.shape[:2], calibration_params)
                        else:
                            logger.warning(f"Pothole detection {det_idx} had a mask but no contours found.")
                    else:
                        logger.warning(f"Pothole detection {det_idx} has no segmentation mask. Measurement will be basic.")
                        px_to_mm_x, px_to_mm_y = get_pixel_to_mm_ratio(calibration_params, image_height=img_height, point_in_image=(rect_box[0] + rect_box[2]//2, rect_box[1] + rect_box[3]//2))
                        measurements_data['estimated_diameter_mm'] = ((rect_box[2] * px_to_mm_x) + (rect_box[3] * px_to_mm_y)) / 2
                        measurements_data['area_sq_m'] = (rect_box[2] * px_to_mm_x * rect_box[3] * px_to_mm_y) / (1000*1000)

                elif distress_type_str == 'rutting':
                    measurements_data = analyze_rutting_bbox(undistorted_image, rect_box, calibration_params)
                else:
                    logger.warning(f"No specific measurement module for distress type: {distress_type_str}. Basic BBox metrics.")
                    px_to_mm_x, px_to_mm_y = get_pixel_to_mm_ratio(calibration_params, image_height=img_height, point_in_image=(rect_box[0] + rect_box[2]//2, rect_box[1] + rect_box[3]//2))
                    measurements_data['bbox_width_mm'] = rect_box[2] * px_to_mm_x
                    measurements_data['bbox_height_mm'] = rect_box[3] * px_to_mm_y
                    measurements_data['area_sq_m'] = (rect_box[2] * px_to_mm_x * rect_box[3] * px_to_mm_y) / (1000*1000)

                if 'area_sq_m' not in measurements_data and 'area_sq_m_bbox' in measurements_data:
                    measurements_data['area_sq_m'] = measurements_data['area_sq_m_bbox']
                elif 'area_sq_m' not in measurements_data and 'width_mm' in measurements_data and 'length_mm' in measurements_data:
                     measurements_data['area_sq_m'] = (measurements_data['width_mm'] * measurements_data['length_mm']) / (1000*1000)
                elif 'area_sq_m' not in measurements_data:
                    measurements_data['area_sq_m'] = 0

                try:
                    distress_enum_type = DistressType(distress_type_str)
                except ValueError:
                    logger.warning(f"Unknown distress type string '{distress_type_str}'. Defaulting to UNKNOWN_DISTRESS.")
                    distress_enum_type = DistressType.UNKNOWN_DISTRESS

                distress = PavementDistress(
                    type=distress_enum_type,
                    location= raw_det.get('bbox'),
                    measurements=measurements_data, 
                    confidence=score
                )
                processed_distresses.append(distress)
            except Exception as e_meas:
                logger.error(f"Error processing or measuring detection {det_idx} ({raw_det.get('type')}): {e_meas}", exc_info=True)

        # 5. Calculate PCI score
        distress_data_for_pci = [
            {
                'type': d.type.value,
                'measurements': d.measurements,
                'severity': d.measurements.get('severity', 'LOW')
            }
            for d in processed_distresses
        ]
        pci_score = await _run_in_executor(
            calculate_pci,
            distress_data_for_pci
        )
        logger.info(f"Calculated PCI score: {pci_score:.2f}")

        # 6. Save analyzed image (with detections drawn)
        final_image_for_report_path = image_output_dir / f"{base_filename}_base_image.png"
        cv2.imwrite(str(final_image_for_report_path), undistorted_image)
        logger.info(f"Base image for report saved to: {final_image_for_report_path}")

        # 7. Generate report (JSON and visualizations)
        report_files_dict = await _run_in_executor(
            generate_analysis_report,
            image_path=str(final_image_for_report_path),
            distresses=[dist.model_dump() for dist in processed_distresses],
            pci_score=pci_score,
            output_dir=str(report_output_dir),
            base_filename=base_filename
        )
        logger.info(f"Analysis report generated: {report_files_dict.get('report')}")

        # Construct response
        static_image_path_prefix = Path("/static") / Path(config["image_output_dir"]).name
        static_report_path_prefix = Path("/static") / Path(config["report_output_dir"]).name

        vis_image_url = None
        if report_files_dict.get('visualization'):
            vis_image_url = str(static_image_path_prefix / Path(report_files_dict['visualization']).name)
        
        report_json_url = None
        if report_files_dict.get('report'):
            report_json_url = str(static_report_path_prefix / Path(report_files_dict['report']).name)

        analysis_response = PavementAnalysisResponse(
            distresses=processed_distresses,
            pci_score=pci_score,
            image_url=vis_image_url,
            report_url=report_json_url
        )
        if report_files_dict.get('plot'):
            analysis_response.plot_url = str(static_report_path_prefix / Path(report_files_dict['plot']).name)

        logger.info(f"Successfully completed pavement analysis for image: {image_filename}")
        return analysis_response

    except ValueError as ve:
        logger.error(f"ValueError during pavement analysis: {ve}", exc_info=True)
        raise
    except RuntimeError as re:
        logger.error(f"RuntimeError during pavement analysis: {re}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Unexpected error analyzing pavement image {image_filename}: {e}", exc_info=True)
        raise RuntimeError(f"Analysis failed for {image_filename}: {str(e)}")
