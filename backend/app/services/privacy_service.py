"""Privacy masking on export (feature 3).

Gaussian-blurs caller-supplied boxes (faces/plates). No detector model is
bundled: callers pass boxes from their own detector, or the whole frame is
left untouched and the response flags masked=false. Disabled by default
means export paths keep serving the original until an operator opts in.
"""
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

    def mask_evidence_snapshot(
        self,
        src: Path,
        dest: Path,
        boxes: Optional[List[Box]] = None,
    ) -> Dict[str, Any]:
        """Write a masked copy of a snapshot to `dest`.

        Returns a report describing what actually happened, so the caller can
        record it in the manifest. The critical property: when no boxes are
        available the report says `masked: false` with a reason. It never
        returns a masked=True for an unmasked file.
        """
        report: Dict[str, Any] = {"masked": False, "boxes": 0, "reason": None}

        # Decide box availability FIRST. "No boxes" is decisive on its own and
        # must not be masked by an unrelated dependency failure -- a caller
        # reading this report needs the real reason the release is unmasked.
        derived = boxes if boxes is not None else self._boxes_from_sidecar(src)
        if not derived:
            report["reason"] = "no face/plate boxes available to mask"
            return report
        if not self.enabled:
            report["reason"] = "masking disabled or not applicable"
            return report

        try:
            import cv2
            img = cv2.imread(str(src))
            if img is None:
                report["reason"] = f"unreadable image: {src.name}"
                return report
        except Exception as e:
            report["reason"] = f"cv2 unavailable: {e}"
            return report

        out, masked = self.mask_image(img, derived)
        if not masked:
            report["reason"] = "masking disabled or not applicable"
            return report
        dest.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(dest), out)
        report["masked"] = True
        report["boxes"] = len(derived)
        report["dest"] = str(dest)
        return report

    @staticmethod
    def _boxes_from_sidecar(src: Path) -> List[Box]:
        """Read boxes from `<snapshot>.boxes.json` if the detector wrote one.

        This is the honest seam: masking is driven by real detector output when
        it exists, and reports honestly when it does not, rather than
        inventing boxes that would mask the wrong pixels.
        """
        sidecar = src.with_suffix(src.suffix + ".boxes.json")
        if not sidecar.is_file():
            return []
        try:
            data = json.loads(sidecar.read_text())
        except Exception:
            return []
        raw = data.get("boxes") if isinstance(data, dict) else data
        if not isinstance(raw, list):
            return []
        out: List[Box] = []
        for item in raw:
            try:
                if len(item) == 4:
                    out.append(tuple(int(v) for v in item))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                continue
        return out

    def mask_image(self, img, boxes: List[Box]):
        """Returns (img, masked_bool). No-op passthrough when disabled or no boxes."""
        if not self.enabled or not boxes:
            return img, False
        try:
            return blur_boxes_bgr(img, boxes, self.blur_kernel), True
        except Exception as e:
            logger.error(f"Privacy masking failed: {e}")
            return img, False
