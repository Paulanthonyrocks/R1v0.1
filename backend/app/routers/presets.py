import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependency_injection import (
    get_current_admin,
    get_current_viewer,
    get_feed_manager,
    get_preset_service,
)
from app.models.common import APIResponse
from app.models.feeds import FeedStatusData
from app.models.presets import Preset, PresetCreate, PresetUpdate
from app.models.user import User
from app.services.feed_manager import FeedManager
from app.services.preset_service import PresetService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/",
    response_model=APIResponse[List[Preset]],
    summary="List Warden Presets",
)
async def list_presets(
    svc: PresetService = Depends(get_preset_service),
    current_user: User = Depends(get_current_viewer),
) -> APIResponse[List[Preset]]:
    presets = await svc.list_presets()
    return APIResponse.success(data=presets, message=f"{len(presets)} presets saved.")


@router.post(
    "/",
    response_model=APIResponse[Preset],
    status_code=status.HTTP_201_CREATED,
    summary="Save a Warden Preset (named camera group)",
)
async def create_preset(
    request: PresetCreate,
    svc: PresetService = Depends(get_preset_service),
    current_user: User = Depends(get_current_admin),
) -> APIResponse[Preset]:
    logger.info(f"Admin {current_user.username} saving preset '{request.name}' "
                f"({len(request.feed_ids)} feeds)")
    preset = await svc.create_preset(request)
    return APIResponse.success(data=preset, message=f"Preset '{preset.name}' saved as {preset.id}.")


@router.get(
    "/{preset_id}",
    response_model=APIResponse[Preset],
    summary="Get a Warden Preset",
)
async def get_preset(
    preset_id: str,
    svc: PresetService = Depends(get_preset_service),
    current_user: User = Depends(get_current_viewer),
) -> APIResponse[Preset]:
    preset = await svc.get_preset(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail=f"Preset '{preset_id}' not found")
    return APIResponse.success(data=preset)


@router.get(
    "/{preset_id}/feeds",
    response_model=APIResponse[List[FeedStatusData]],
    summary="Live feed statuses for a preset's camera group",
)
async def preset_feeds(
    preset_id: str,
    svc: PresetService = Depends(get_preset_service),
    feed_manager: FeedManager = Depends(get_feed_manager),
    current_user: User = Depends(get_current_viewer),
) -> APIResponse[List[FeedStatusData]]:
    preset = await svc.get_preset(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail=f"Preset '{preset_id}' not found")
    statuses = await feed_manager.get_all_statuses()
    members = [s for s in statuses if s.feed_id in set(preset.feed_ids)]
    return APIResponse.success(
        data=members,
        message=f"{len(members)}/{len(preset.feed_ids)} preset feeds live.",
    )


@router.patch(
    "/{preset_id}",
    response_model=APIResponse[Preset],
    summary="Update a Warden Preset",
)
async def update_preset(
    preset_id: str,
    request: PresetUpdate,
    svc: PresetService = Depends(get_preset_service),
    current_user: User = Depends(get_current_admin),
) -> APIResponse[Preset]:
    preset = await svc.update_preset(preset_id, request)
    if not preset:
        raise HTTPException(status_code=404, detail=f"Preset '{preset_id}' not found")
    return APIResponse.success(data=preset, message=f"Preset '{preset.name}' updated.")


@router.delete(
    "/{preset_id}",
    response_model=APIResponse[dict],
    summary="Delete a Warden Preset",
)
async def delete_preset(
    preset_id: str,
    svc: PresetService = Depends(get_preset_service),
    current_user: User = Depends(get_current_admin),
) -> APIResponse[dict]:
    deleted = await svc.delete_preset(preset_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Preset '{preset_id}' not found")
    return APIResponse.success(data={"deleted": preset_id}, message=f"Preset '{preset_id}' deleted.")
