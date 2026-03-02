import cv2
import numpy as np
import logging
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("app.ml.ocr")

class LocalOCR:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.ocr_cfg = config.get("ocr_engine", {})
        self.languages = self.ocr_cfg.get("languages", ["en"])
        # Prioritize OCR specific GPU flag, fallback to global performance flag
        self.gpu = self.ocr_cfg.get("use_gpu_ocr", config.get("performance", {}).get("gpu_acceleration", False))
        
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

        def read_plate(self, image: np.ndarray) -> Optional[Tuple[str, float]]:
            """
            Reads license plate text from a cropped image and returns (text, confidence).
            """
            if self.reader is None:
                return None
    
            try:
                # Using detail=1 to get (bbox, text, confidence)
                results = self.reader.readtext(image, detail=1)
                
                if not results:
                    return None
                
                # Combine snippets and average confidence
                texts = []
                confs = []
                for (bbox, text, conf) in results:
                    clean_text = text.replace(" ", "").upper()
                    if len(clean_text) >= 1:
                        texts.append(clean_text)
                        confs.append(conf)
                
                combined = "".join(texts)
                avg_conf = sum(confs) / len(confs) if confs else 0.0
                
                # Basic validation: plates usually have at least 3 chars
                if len(combined) >= 3:
                    return combined, avg_conf
                return None
                
            except Exception as e:
                logger.error(f"EasyOCR read failed: {e}")
                return None
    