from fastapi import APIRouter, UploadFile, File, HTTPException, Depends
from typing import List, Optional
from ..ml.pavement_analysis.analyze_pavement import analyze_pavement_image
from ..models.pavement import PavementAnalysisResponse
from ..dependencies import get_current_active_user

router = APIRouter(
    prefix="/api/pavement",
    tags=["pavement"],
    responses={404: {"description": "Not found"}},
)

@router.post("/analyze", response_model=PavementAnalysisResponse)
async def analyze_pavement(
    image: UploadFile = File(...),
    current_user: dict = Depends(get_current_active_user)
):
    """
    Analyze pavement image for distresses and defects
    """
    try:
        contents = await image.read()
        results = await analyze_pavement_image(contents)
        return results
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
