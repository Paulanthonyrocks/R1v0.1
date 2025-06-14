import cv2
import numpy as np
import logging
import io
import time
from typing import Dict, Optional, Any # Ensure Any is imported if used, though not directly in LPP
from PIL import Image
import pytesseract
import google.generativeai as genai
from google.api_core import exceptions as google_api_exceptions
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type, RetryError

logger = logging.getLogger(__name__)

class LicensePlatePreprocessor:
    def __init__(self, config: Dict, perspective_matrix: Optional[np.ndarray] = None): # perspective_matrix not used in current impl
        self.config = config.get("ocr_engine", {})
        self.gemini_api_key = self.config.get("gemini_api_key")
        self.model = None
        if self.gemini_api_key:
            try:
                genai.configure(api_key=self.gemini_api_key)
                self.model = genai.GenerativeModel('gemini-pro-vision')
                logger.info("Gemini Pro Vision model initialized for OCR.")
            except Exception as e:
                logger.error(f"Failed to initialize Gemini Pro Vision model: {e}", exc_info=True)
                self.model = None
        else:
            logger.warning("Gemini API key not provided. Gemini OCR will not be available.")

        self.cool_down_secs = self.config.get("gemini_cool_down_secs", 60)
        self.last_api_error_time = 0 # Stores time of last API error to implement cool-down

        # Ensure kernels are numpy arrays with correct dtype
        self.morph_kernel = np.array(self.config.get("morph_kernel", [[1,1,1],[1,1,1],[1,1,1]]), dtype=np.uint8)
        self.sharpen_kernel = np.array(self.config.get("sharpen_kernel", [[-1,-1,-1],[-1,9,-1],[-1,-1,-1]]), dtype=np.float32)

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_attempt(3), # Matches the value from original config, adjustable
        retry=retry_if_exception_type((
            google_api_exceptions.PermissionDenied,
            google_api_exceptions.ResourceExhausted,
            google_api_exceptions.DeadlineExceeded,
            google_api_exceptions.InternalServerError,
            google_api_exceptions.ServiceUnavailable,
            google_api_exceptions.Aborted,
            google_api_exceptions.Unknown,
            ConnectionError,
            TimeoutError
            # Not retrying on genai.types.BlockedPromptException or genai.types.StopCandidateException
        ))
    )
    def _call_gemini_ocr(self, image_roi: np.ndarray) -> str:
        if not self.model:
             logger.warning("Gemini model not available for _call_gemini_ocr.")
             return ""

        current_time = time.monotonic()
        if current_time - self.last_api_error_time < self.cool_down_secs:
            logger.info(f"Gemini API cool-down period active. Skipping OCR attempt. Wait {self.cool_down_secs - (current_time - self.last_api_error_time):.1f}s.")
            return ""

        logger.debug(f"Attempting Gemini OCR call for ROI of shape {image_roi.shape}")
        try:
            pil_image = Image.fromarray(cv2.cvtColor(image_roi, cv2.COLOR_BGR2RGB))
            img_byte_arr = io.BytesIO()
            pil_image.save(img_byte_arr, format='JPEG', quality=90) # Good quality JPEG
            img_bytes = img_byte_arr.getvalue()

            image_part = {"mime_type": "image/jpeg", "data": img_bytes}
            # Updated prompt based on common best practices for Gemini Vision for this task
            prompt_parts = [
                image_part,
                "Identify and extract the license plate number from this image. Provide only the license plate characters (alphanumeric). Do not include any additional text, labels, or explanations. If multiple plates are visible, focus on the largest and clearest one. If no license plate is clearly visible or readable, respond with an empty string.",
            ]

            # Assuming generate_content is the correct method for gemini-pro-vision
            response = self.model.generate_content(prompt_parts)

            ocr_text = ""
            if response and hasattr(response, 'text') and response.text:
                ocr_text = response.text.strip()
            elif response and response.candidates and response.candidates[0].content and response.candidates[0].content.parts:
                ocr_text = response.candidates[0].content.parts[0].text.strip()

            # Post-process to keep only alphanumeric, uppercase
            ocr_text = ''.join(filter(str.isalnum, ocr_text)).upper()

            if ocr_text:
                logger.info(f"Gemini OCR Result: '{ocr_text}'")
            else:
                logger.debug("Gemini OCR: No plate found or empty result after processing.")

            self.last_api_error_time = 0 # Reset cool-down on success
            return ocr_text

        except (genai.types.BlockedPromptException, genai.types.StopCandidateException) as safety_error:
            logger.warning(f"Gemini content safety issue: {safety_error}")
            self.last_api_error_time = time.monotonic() # Start cool-down
            return "" # Not retryable by Tenacity for this type of error
        except (google_api_exceptions.PermissionDenied,
                google_api_exceptions.ResourceExhausted,
                google_api_exceptions.DeadlineExceeded,
                google_api_exceptions.InternalServerError,
                google_api_exceptions.ServiceUnavailable,
                google_api_exceptions.Aborted,
                google_api_exceptions.Unknown,
                ConnectionError,
                TimeoutError) as retryable_error:
            logger.warning(f"Gemini API/network error (will be retried by tenacity): {type(retryable_error).__name__} - {retryable_error}")
            self.last_api_error_time = time.monotonic() # Start cool-down after retries eventually fail
            raise # Re-raise for tenacity to handle retries
        except Exception as e:
            logger.error(f"Unexpected error during Gemini OCR call: {e}", exc_info=True)
            self.last_api_error_time = time.monotonic() # Start cool-down
            return ""

    def _preprocess_for_tesseract(self, roi: np.ndarray) -> Optional[np.ndarray]:
        if roi is None or roi.size == 0 or roi.shape[0] < 10 or roi.shape[1] < 10: # Basic check
            logger.debug("ROI too small or empty for Tesseract preprocessing.")
            return None
        try:
            if len(roi.shape) == 3:
                gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
            else:
                gray = roi.copy() # Assume already grayscale if not 3 channels

            # Noise reduction - GaussianBlur is common
            blurred = cv2.GaussianBlur(gray, (3, 3), 0)

            # Thresholding - Adaptive is often better for varying lighting
            # Ensure THRESH_BINARY_INV is used if Tesseract expects white text on black bg
            thresh = cv2.adaptiveThreshold(blurred, 255,
                                           cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                           cv2.THRESH_BINARY_INV, 19, 9)

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

        # --- Attempt Gemini OCR first if available and configured ---
        if self.model and self.gemini_api_key:
            logger.debug("Attempting OCR using Gemini...")
            try:
                ocr_result = self._call_gemini_ocr(roi) # This method is wrapped with @retry
            except RetryError as e: # Tenacity: All retries failed for _call_gemini_ocr
                logger.error(f"Gemini OCR failed after all retries. Last error: {e.last_attempt.exception()}. ROI shape: {roi.shape}")
                ocr_result = ""
            except Exception as e: # Catch any other unexpected error from _call_gemini_ocr
                logger.error(f"Unexpected error during Gemini OCR attempt sequence: {e}", exc_info=True)
                ocr_result = ""

            if ocr_result: # If Gemini found something, return it.
                 logger.info(f"Gemini OCR successful, result: '{ocr_result}'")
                 return ocr_result
            else: # Gemini failed or found nothing, log and fall through to Tesseract.
                 logger.info("Gemini OCR did not yield a result or failed. Falling back to Tesseract if available.")

        # --- Fallback to Tesseract OCR ---
        # Check if pytesseract module itself was successfully imported
        if 'pytesseract' not in globals() and 'pytesseract' not in locals():
            # This check might be tricky depending on how pytesseract is imported (e.g. import pytesseract vs from foo import pytesseract)
            # A more robust check is to see if the variable 'pytesseract' exists and is not None.
            # However, the original code checked `if not pytesseract:` which assumes `import pytesseract` was attempted.
             if not (self.model and self.gemini_api_key): # Only log this warning if Gemini wasn't tried/configured
                 logger.warning("Pytesseract module not available. No OCR will be performed.")
             return ""

        logger.debug("Attempting OCR using Tesseract...")
        processed_roi_for_tesseract = self._preprocess_for_tesseract(roi)

        if processed_roi_for_tesseract is not None:
            try:
                # Standard Tesseract config for license plates
                custom_config = r'--oem 3 --psm 7 -c tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
                # Add a timeout to Tesseract call to prevent indefinite blocking
                text = pytesseract.image_to_string(processed_roi_for_tesseract, config=custom_config, timeout=5)

                ocr_result = ''.join(filter(str.isalnum, text)).upper() # Clean and format
                if ocr_result:
                    logger.info(f"Tesseract OCR Raw: '{text.strip()}', Processed: '{ocr_result}'")
                else:
                    logger.debug("Tesseract OCR: No text found or empty result after processing.")
            except RuntimeError as e: # Catches Tesseract not found, timeout, or other runtime issues
                logger.error(f"Tesseract runtime error: {e}", exc_info=False)
                ocr_result = ""
            except Exception as e: # Catch any other unexpected Tesseract error
                logger.error(f"Tesseract OCR unexpected error: {e}", exc_info=True)
                ocr_result = ""
        else:
            logger.warning("Preprocessing for Tesseract failed or yielded None, cannot perform Tesseract OCR.")
            ocr_result = "" # Ensure it's empty if preprocessing fails

        return ocr_result
