# backend/app/routers/pavement.py
"""Pavement-condition API — wires the previously uncalled
app/ml/pavement_analysis package to HTTP (capability review item 4).

Heavy deps (cv2/ultralytics/matplotlib) are imported lazily inside the
endpoint so this router — and therefore app boot — never depends on them.
"""
import logging
from pathlib import Path
from typing import Any, Dict, List

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

logger = logging.getLogger(__name__)

router = APIRouter()


def _pavement_dirs():
    """(image_dir, report_dir) from config storage.*, backend-aware."""
    image_dir = Path("data/pavement_images")
    report_dir = Path("data/pavement_reports")
    root = None
    try:
        from app.config import get_current_config
        cfg = get_current_config()
        storage = cfg.storage or {}
        image_dir = Path(storage.get("image_output_dir", image_dir))
        report_dir = Path(storage.get("report_output_dir", report_dir))
        root = Path(cfg.project_root_dir)
    except Exception:
        root = None
    if root is not None and not image_dir.is_absolute() and (root / "backend").is_dir():
        image_dir = root / "backend" / image_dir
        report_dir = root / "backend" / report_dir
    return image_dir, report_dir


@router.post(
    "/analyze",
    summary="Analyze a pavement image for distresses",
    description="Runs the pavement-analysis package (distress detection, crack/pothole/rutting "
                "measurement, PCI) on an uploaded image and returns distresses + PCI.",
)
async def analyze_pavement(file: UploadFile = File(...)):
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty upload.")
    try:
        from app.ml.pavement_analysis import analyze_pavement_image
    except Exception as e:
        logger.error(f"Pavement analysis module unavailable: {e}", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail=f"Pavement analysis unavailable on this host (missing deps?): {e}",
        )
    try:
        return await analyze_pavement_image(data, file.filename or "upload.png")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/reports", summary="List generated pavement reports")
async def list_reports() -> Dict[str, Any]:
    _, report_dir = _pavement_dirs()
    reports: List[Dict[str, Any]] = []
    if report_dir.is_dir():
        for p in sorted(report_dir.iterdir()):
            if p.is_file():
                st = p.stat()
                reports.append({"name": p.name, "size": st.st_size, "modified": st.st_mtime})
    return {"reports": reports, "report_dir": str(report_dir)}


@router.get("/reports/{name}", summary="Download a generated pavement report")
async def get_report(name: str):
    _, report_dir = _pavement_dirs()
    base = report_dir.resolve()
    p = (base / name).resolve()
    if base not in p.parents or not p.is_file():
        raise HTTPException(status_code=404, detail="Report not found.")
    return FileResponse(str(p))
