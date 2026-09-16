"""Privacy masking on export (feature 3).

Gaussian-blurs caller-supplied boxes (faces/plates). No detector model is
bundled: callers pass boxes from their own detector, or the whole frame is
left untouched and the response flags masked=false. Disabled by default
means export paths keep serving the original until an operator opts in.
"""
import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger("app.services.privacy")

Box = Tuple[int, int, int, int]


def blur_boxes_bgr(img, boxes: List[Box], ksize: int = 31):
    """Blur each (x1,y1,x2,y2) region in-place on a BGR numpy image. Returns img."""
    import cv2

    h, w = img.shape[:2]
    k = max(3, ksize | 1)  # odd kernel
    for (x1, y1, x2, y2) in boxes:
        x1c, y1c = max(0, x1), max(0, y1)
        x2c, y2c = min(w, x2), min(h, y2)
        if x2c <= x1c or y2c <= y1c:
            continue
        roi = img[y1c:y2c, x1c:x2c]
        img[y1c:y2c, x1c:x2c] = cv2.GaussianBlur(roi, (k, k), 0)
    return img


class PrivacyService:
    def __init__(self, config: Dict[str, Any]):
        cfg = config.get("privacy", {})
        self.enabled = cfg.get("enabled", False)
        self.blur_kernel = int(cfg.get("blur_kernel", 31))

    def mask_image(self, img, boxes: List[Box]):
        """Returns (img, masked_bool). No-op passthrough when disabled or no boxes."""
        if not self.enabled or not boxes:
            return img, False
        try:
            return blur_boxes_bgr(img, boxes, self.blur_kernel), True
        except Exception as e:
            logger.error(f"Privacy masking failed: {e}")
            return img, False
