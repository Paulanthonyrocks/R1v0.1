import cv2
import numpy as np
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("app.ml.ocr")

class LocalOCR:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.ocr_cfg = config.get("ocr_engine", {})
        self.languages = self.ocr_cfg.get("languages", ["en"])
        # Prioritize OCR specific GPU flag, fallback to global performance flag
        self.gpu = self.ocr_cfg.get("use_gpu_ocr", config.get("performance", {}).get("gpu_acceleration", False))
        # EasyOCR's default recognizer tokenizes text as WORDS, which drops
        # license-plate-format strings. Constrain it to plate characters so it
        # reads "ABC123" / "AB-123" style plates instead of rejecting them.
        self.allowlist = self.ocr_cfg.get(
            "plate_allowlist", "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"
        )
        
        logger.info(f"Initializing EasyOCR with languages {self.languages} (GPU: {self.gpu})")
        try:
            import easyocr
            self.reader = easyocr.Reader(self.languages, gpu=self.gpu)
            logger.info("EasyOCR initialized successfully.")
        except ImportError:
            logger.error("EasyOCR library not found. Please install it with 'pip install easyocr'.")
            self.reader = None
        except Exception as e:
            logger.error(f"Failed to initialize EasyOCR: {e}")
            self.reader = None

    def read_plate(self, image: np.ndarray) -> Optional[str]:
        """
        Reads license plate text from a cropped image.
        """
        if self.reader is None:
            return None

        try:
            # EasyOCR works best with BGR or RGB. Our CoreModule provides RGB.
            # We'll use detail=0 to just get the text strings; the allowlist
            # constrains recognition to plate characters (A-Z0-9-).
            results = self.reader.readtext(image, detail=0, allowlist=self.allowlist)
            
            if not results:
                return None
            
            # Combine results, usually it's just one or two snippets for a plate
            # Clean up non-alphanumeric chars often found in plates
            combined = "".join(results).replace(" ", "").upper()
            
            # Basic validation: plates usually have at least 3 chars
            if len(combined) >= 3:
                return combined
            return None
            
        except Exception as e:
            logger.error(f"EasyOCR read failed: {e}")
            return None