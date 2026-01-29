# backend/app/ml/segmentation/edgetam.py
import logging
import time
from typing import List, Dict, Any

import numpy as np
import torch
from transformers import Sam2Processor, EdgeTamModel
import cv2

logger = logging.getLogger(__name__)

class EdgeTAMSegmenter:
    def __init__(self, model_path: str = "facebook/EdgeTAM", use_gpu: bool = False):
        self.model_path = model_path
        self.device = "cuda" if use_gpu and torch.cuda.is_available() else "cpu"
        logger.info(f"Initializing EdgeTAMSegmenter on device: {self.device}")
        
        self.processor = None
        self.model = None
        self._load_model()

    def _load_model(self):
        try:
            start_time = time.time()
            self.processor = Sam2Processor.from_pretrained(self.model_path, subfolder="checkpoints/mobile_sam")
            self.model = EdgeTamModel.from_pretrained(self.model_path, subfolder="checkpoints/mobile_sam")
            self.model.to(self.device)
            self.model.eval()
            load_time = time.time() - start_time
            logger.info(f"EdgeTAM model loaded from {self.model_path} on '{self.device}' in {load_time:.3f}s")
        except Exception as e:
            logger.error(f"Failed to load EdgeTAM model: {e}", exc_info=True)
            raise RuntimeError(f"Model loading failed: {e}")

    def segment_frame(self, frame: np.ndarray) -> Dict[str, Any]:
        """
        Performs segmentation on a single frame.
        """
        try:
            inputs = self.processor(frame, return_tensors="pt").to(self.device)
            with torch.no_grad():
                outputs = self.model(**inputs)
            
            masks = self.processor.postprocess_masks(
                outputs.pred_masks,
                inputs["original_sizes"],
                inputs["reshaped_input_sizes"],
            )[0]
            
            return {"masks": masks.cpu().numpy()}
        except Exception as e:
            logger.error(f"Error during EdgeTAM segmentation: {e}", exc_info=True)
            return {}

    def masks_to_bboxes(self, masks: np.ndarray) -> List[List[int]]:
        """
        Converts segmentation masks to bounding boxes.
        """
        if masks is None or masks.size == 0:
            return []
        
        bboxes = []
        for mask in masks:
            # Assuming mask is a 2D numpy array
            rows = np.any(mask, axis=1)
            cols = np.any(mask, axis=0)
            if not np.any(rows) or not np.any(cols):
                continue

            rmin, rmax = np.where(rows)[0][[0, -1]]
            cmin, cmax = np.where(cols)[0][[0, -1]]
            bboxes.append([int(cmin), int(rmin), int(cmax), int(rmax)])
        return bboxes

if __name__ == "__main__":
    # Example usage for testing
    logging.basicConfig(level=logging.INFO)
    
    # Create a dummy frame
    dummy_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    cv2.putText(
        dummy_frame,
        "Test Frame for EdgeTAM",
        (50, 240),
        cv2.FONT_HERSHEY_SIMPLEX,
        1,
        (255, 255, 255),
        2,
    )

    try:
        segmenter = EdgeTAMSegmenter(use_gpu=True)
        logger.info("EdgeTAMSegmenter initialized successfully.")
        
        # Test segmentation
        segments = segmenter.segment_frame(dummy_frame)
        logger.info(f"Segmentation result: {segments.keys()}")

        if "masks" in segments:
            # Test mask to bbox conversion
            bboxes = segmenter.masks_to_bboxes(segments["masks"])
            logger.info(f"Bounding boxes from segmentation masks: {bboxes}")

    except Exception as e:
        logger.error(f"An error occurred during the EdgeTAM test: {e}", exc_info=True)
