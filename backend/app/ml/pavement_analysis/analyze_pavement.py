import cv2
import numpy as np
import logging
from pathlib import Path
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Any
import os
from datetime import datetime

from .reporting.report_generator import generate_analysis_report
from ...models.pavement import PavementAnalysisResponse, PavementDistress, Measurement, DistressType
from .utils.camera_calibration import load_calibration_params, undistort_image, get_pixel_to_mm_ratio
from .utils.image_preprocessing import preprocess_image, apply_roi
from .detection_modules.ml_detector import load_ml_model, detect_distresses_ml
from .analysis_modules.pci_calculator import calculate_pci

# Initialize logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize thread pool for CPU-intensive operations
thread_pool = ThreadPoolExecutor()

# Load ML model once at module level
model = load_ml_model()

async def analyze_pavement_image(image_data: bytes) -> PavementAnalysisResponse:
    """
    Asynchronously analyze pavement image for distresses
    """
    try:
        # Convert bytes to numpy array
        nparr = np.frombuffer(image_data, np.uint8)
        image = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

        # Generate timestamp for unique file naming
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Define storage paths
        config = {
            "image_output_dir": "./data/pavement_images",
            "report_output_dir": "./data/pavement_reports"
        }
        image_output_dir = Path(config["image_output_dir"])
        report_output_dir = Path(config["report_output_dir"])
        image_output_dir.mkdir(parents=True, exist_ok=True)
        report_output_dir.mkdir(parents=True, exist_ok=True)

        # Save the original image
        original_image_path = image_output_dir / f"{timestamp}_original.png"
        cv2.imwrite(str(original_image_path), image)

        # Get the event loop
        loop = asyncio.get_event_loop()

        # Preprocess image
        processed_image = await loop.run_in_executor(
            thread_pool, 
            preprocess_image,
            image
        )

        # Detect distresses using ML model
        detections = await loop.run_in_executor(
            thread_pool,
            detect_distresses_ml,
            processed_image,
            model
        )

        # Calculate PCI score
        pci_score = await loop.run_in_executor(
            thread_pool,
            calculate_pci,
            detections
        )

        # Save analyzed image
        analyzed_image_path = image_output_dir / f"{timestamp}_analyzed.png"
        cv2.imwrite(str(analyzed_image_path), processed_image)

        # Generate report
        report_files = generate_analysis_report(
            str(original_image_path),
            detections,
            pci_score,
            str(report_output_dir)
        )

        # Convert detections to response model
        distresses = []
        for detection in detections:
            distress = PavementDistress(
                type=detection['type'],
                location=detection['bbox'],
                measurements=Measurement(**detection['measurements']),
                confidence=detection['confidence']
            )
            distresses.append(distress)

        return PavementAnalysisResponse(
            distresses=distresses,
            pci_score=pci_score,
            image_url=f"/data/pavement_images/{timestamp}_analyzed.png",
            report_url=f"/data/pavement_reports/{timestamp}_report.json"
        )

    except Exception as e:
        logger.error(f"Error analyzing pavement image: {str(e)}")
        raise
