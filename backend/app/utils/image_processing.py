import cv2
import numpy as np
import logging
import io
import time
from typing import (
    Dict,
    Optional,
)  # Ensure Any is imported if used, though not directly in LPP
from PIL import Image
from tenacity import (
    retry,
    wait_exponential,
    stop_after_attempt,
    retry_if_exception_type,
    RetryError,
)

logger = logging.getLogger(__name__)


class LicensePlatePreprocessor:
    def __init__(
        self, config: Dict, perspective_matrix: Optional[np.ndarray] = None
    ):  # perspective_matrix not used in current impl
        self.config = config.get("ocr_engine", {})
        self.gemini_api_key = self.config.get("gemini_api_key")
        self.use_gemini_ocr = self.config.get("use_gemini_ocr", True) # Default to True for backward compatibility
        self.use_tesseract = self.config.get("use_tesseract", False) # Default to False
        self.client = None
        self.model_id = "gemini-1.5-flash"
        if self.gemini_api_key and self.use_gemini_ocr:
            try:
                from google import genai
                self.client = genai.Client(api_key=self.gemini_api_key)
                logger.info(f"Gemini {self.model_id} client initialized for OCR.")
            except ImportError:
                logger.error("google-genai library not found. Gemini OCR will not be available.")
                self.client = None
            except Exception as e:
                logger.error(
                    f"Failed to initialize Gemini client: {e}", exc_info=True
                )
                self.client = None
        elif self.use_gemini_ocr:
            logger.warning(
                "Gemini API key not provided. Gemini OCR will not be available."
            )
        else:
            logger.info("Gemini OCR explicitly disabled in config.")

        self.cool_down_secs = self.config.get("gemini_cool_down_secs", 60)
        self.last_api_error_time = (
            0  # Stores time of last API error to implement cool-down
        )

        # Ensure kernels are numpy arrays with correct dtype
        self.morph_kernel = np.array(
            self.config.get("morph_kernel", [[1, 1, 1], [1, 1, 1], [1, 1, 1]]),
            dtype=np.uint8,
        )
        self.sharpen_kernel = np.array(
            self.config.get(
                "sharpen_kernel", [[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]]
            ),
            dtype=np.float32,
        )
        self.min_roi_size = self.config.get(
            "min_roi_size", 100
        )  # Default to 100 if not specified

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(
            3
        ),  # Matches the value from original config, adjustable
        retry=retry_if_exception_type(
            (
                google_api_exceptions.PermissionDenied,
                google_api_exceptions.ResourceExhausted,
                google_api_exceptions.DeadlineExceeded,
                google_api_exceptions.InternalServerError,
                google_api_exceptions.ServiceUnavailable,
                google_api_exceptions.Aborted,
                google_api_exceptions.Unknown,
                ConnectionError,
                TimeoutError,
            )
        ),
    )
    def _call_gemini_ocr(self, image_roi: np.ndarray) -> str:
        if not self.client:
            logger.warning("Gemini client not available for _call_gemini_ocr.")
            return ""

        from google.genai import types

        current_time = time.monotonic()
        if current_time - self.last_api_error_time < self.cool_down_secs:
            logger.info(
                f"Gemini API cool-down period active. Skipping OCR attempt. Wait {self.cool_down_secs - (current_time - self.last_api_error_time):.1f}s."
            )
            return ""

        logger.debug(f"Attempting Gemini OCR call for ROI of shape {image_roi.shape}")
        try:
            # --- Grayscale and Contrast Enhancement for OCR ---
            if len(image_roi.shape) == 3:
                gray_roi = cv2.cvtColor(image_roi, cv2.COLOR_BGR2GRAY)
            else:
                gray_roi = image_roi  # Assume already grayscale

            # Apply Contrast Limited Adaptive Histogram Equalization (CLAHE)
            clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
            enhanced_roi = clahe.apply(gray_roi)
            # -------------------------------------------------

            pil_image = Image.fromarray(enhanced_roi)
            img_byte_arr = io.BytesIO()
            pil_image.save(
                img_byte_arr, format="JPEG", quality=95
            )  # Use high quality JPEG for OCR
            img_bytes = img_byte_arr.getvalue()

            prompt = "Identify and extract the license plate number from this image. Provide only the license plate characters (alphanumeric). Do not include any additional text, labels, or explanations. If multiple plates are visible, focus on the largest and clearest one. If no license plate is clearly visible or readable, respond with an empty string."

            response = self.client.models.generate_content(
                model=self.model_id,
                contents=[
                    types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg"),
                    prompt
                ]
            )

            ocr_text = ""
            if response and response.text:
                ocr_text = response.text.strip()

            ocr_text = "".join(filter(str.isalnum, ocr_text)).upper()

            if ocr_text:
                logger.info(f"Gemini OCR Result: '{ocr_text}'")
            else:
                logger.debug(
                    "Gemini OCR: No plate found or empty result after processing."
                )

            self.last_api_error_time = 0
            return ocr_text

        except Exception as e:
            # Check for safety-related blocks if possible, otherwise generic error
            logger.error(f"Error during Gemini OCR call: {e}", exc_info=True)
            self.last_api_error_time = time.monotonic()
            
            # Re-raise if it's one of the retryable exceptions handled by tenacity
            try:
                from google.api_core import exceptions as google_api_exceptions
                if isinstance(e, (
                    google_api_exceptions.PermissionDenied,
                    google_api_exceptions.ResourceExhausted,
                    google_api_exceptions.DeadlineExceeded,
                    google_api_exceptions.InternalServerError,
                    google_api_exceptions.ServiceUnavailable,
                    google_api_exceptions.Aborted,
                    google_api_exceptions.Unknown,
                    ConnectionError,
                    TimeoutError,
                )):
                    raise e
            except ImportError:
                # If google.api_core is not present, we can't check for these specific exceptions
                # but they probably won't be raised anyway if the lib is missing
                pass
            return ""

    def _preprocess_for_tesseract(self, roi: np.ndarray) -> Optional[np.ndarray]:
        if (
            roi is None or roi.size == 0 or roi.shape[0] < 10 or roi.shape[1] < 10
        ):  # Basic check
            logger.debug("ROI too small or empty for Tesseract preprocessing.")
            return None
        try:
            if len(roi.shape) == 3:
                gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            else:
                gray = roi.copy()  # Assume already grayscale if not 3 channels

            # Noise reduction - GaussianBlur is common
            blurred = cv2.GaussianBlur(gray, (3, 3), 0)

            # Thresholding - Adaptive is often better for varying lighting
            # Ensure THRESH_BINARY_INV is used if Tesseract expects white text on black bg
            thresh = cv2.adaptiveThreshold(
                blurred,
                255,
                cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                cv2.THRESH_BINARY_INV,
                19,
                9,
            )

            # Optional: Morphological operations (opening/closing) to remove small noise or fill gaps
            # kernel = self.morph_kernel # Or cv2.getStructuringElement
            # opened = cv2.morphologyEx(thresh, cv2.MORPH_OPEN, kernel, iterations=1)
            # closed = cv2.morphologyEx(opened, cv2.MORPH_CLOSE, kernel, iterations=1)
            # For Tesseract, sometimes simpler is better. Start with just threshold.
            processed_roi = thresh

            return processed_roi
        except Exception as e:
            logger.error(f"Error in _preprocess_for_tesseract: {e}", exc_info=True)
            return None

    def preprocess_and_ocr(self, roi: np.ndarray) -> str:
        ocr_result = ""
        if roi is None or roi.size == 0:
            logger.debug("Received empty ROI for OCR.")
            return ""

        # --- Attempt Gemini OCR first if available and configured (Higher accuracy) ---
        if self.client and self.gemini_api_key and self.use_gemini_ocr:
            logger.debug("Attempting OCR using Gemini...")
            try:
                ocr_result = self._call_gemini_ocr(roi)
            except RetryError as e:
                logger.error(f"Gemini OCR failed after all retries. ROI shape: {roi.shape}")
                ocr_result = ""
            except Exception as e:
                logger.error(f"Unexpected error during Gemini OCR: {e}")
                ocr_result = ""

            if ocr_result:
                logger.info(f"Gemini OCR successful: '{ocr_result}'")
                return ocr_result
            else:
                logger.info("Gemini OCR did not yield a result. Falling back to local Tesseract.")

        # --- Fallback to Tesseract OCR (Local) ---
        # We always attempt Tesseract as a fallback if Gemini fails or is disabled
        logger.debug("Attempting local OCR using Tesseract...")
        processed_roi_for_tesseract = self._preprocess_for_tesseract(roi)

        if processed_roi_for_tesseract is not None:
            try:
                import pytesseract
                # Standard Tesseract config for license plates
                custom_config = r"--oem 3 --psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
                text = pytesseract.image_to_string(
                    processed_roi_for_tesseract, config=custom_config, timeout=5
                )

                ocr_result = "".join(filter(str.isalnum, text)).upper()
                if ocr_result:
                    logger.info(f"Tesseract OCR successful: '{ocr_result}'")
                else:
                    logger.debug("Tesseract OCR: No text found.")
            except ImportError:
                logger.error("pytesseract library not found. Local OCR will not be available.")
                ocr_result = ""
            except Exception as e:
                logger.error(f"Tesseract OCR error: {e}")
                ocr_result = ""
        else:
            logger.warning("Preprocessing for Tesseract failed.")
            ocr_result = ""

        return ocr_result
