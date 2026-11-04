import logging
from typing import List

from fastapi import APIRouter, Depends, status

from app.dependency_injection import (
    get_current_admin,
    get_current_viewer,
    get_feed_manager,
    get_road_service,
    get_traffic_signal_service,
)
from app.models.common import APIResponse
from app.models.feeds import FeedStatusData
from app.models.roads import Road, RoadCreate, RoadSnap, RoadUpdate, SnapRequest
from app.models.user import User
from app.services.feed_manager import FeedManager
from app.services.road_service import RoadService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/",
    response_model=APIResponse[List[Road]],
    summary="List Roads",
)
async def list_roads(
    svc: RoadService = Depends(get_road_service),
    current_user: User = Depends(get_current_viewer),
) -> APIResponse[List[Road]]:
    roads = await svc.list_roads()
    return APIResponse.success(data=roads, message=f"{len(roads)} roads registered.")


@router.post(
    "/",
    response_model=APIResponse[Road],
    status_code=status.HTTP_201_CREATED,
    summary="Register a Road/Corridor",
)
async def create_road(
    request: RoadCreate,
    svc: RoadService = Depends(get_road_service),
    current_user: User = Depends(get_current_admin),
) -> APIResponse[Road]:
    logger.info(f"Admin {current_user.username} registering road: {request.name}")
    road = await svc.create_road(request)
    return APIResponse.success(data=road, message=f"Road '{road.name}' registered as {road.id}.")


@router.post(
    "/snap",
    response_model=APIResponse[RoadSnap],
    summary="Snap a map point to the nearest road",
    description=(
        "Used at camera-placement time: given a lat/lng, returns the closest "
        "registered road within max_distance_m, the projected point on it, "
        "the distance, the arc length along the road, and the local direction "
        "of travel (to orient the camera wedge / match feed headings)."
    ),
)
async def snap_to_road(
    request: SnapRequest,
    svc: RoadService = Depends(get_road_service),
    current_user: User = Depends(get_current_viewer),
) -> APIResponse[RoadSnap]:
    result = svc.snap(request.latitude, request.longitude, request.max_distance_m)
    return APIResponse.success(
        data=RoadSnap(**result),
        message="Snapped to road." if result["found"] else "No road within range.",
    )


@router.get(
    "/assets",
    response_model=APIResponse[dict],
    summary="Signals and work-zones joined onto every road",
)
async def road_assets(
    svc: RoadService = Depends(get_road_service),
    fm: FeedManager = Depends(get_feed_manager),
    sig_svc = Depends(get_traffic_signal_service),
    current_user: User = Depends(get_current_viewer),
) -> APIResponse[dict]:
    """Per road: traffic signals within 80 m of the polyline (with phase and
    nearest intersection node) and active work-zones whose feed is placed on
    the road (with chainage). Declared before /{road_id} so 'assets' is not
    captured as a road id."""
    from app.config import get_current_config
    from app.services.workzone_service import WorkZoneService
    from app.utils.road_assets import signals_on_road, workzones_on_road

    roads = await svc.list_roads()
    signals = [s.model_dump() for s in await sig_svc.get_all_signal_states()]
    zones = WorkZoneService(config=get_current_config().model_dump()).active()

    feed_geo: dict = {}
    for fid, entry in (fm.process_registry or {}).items():
        cfg = entry.get("config_info") if isinstance(entry, dict) else None
        if cfg is None or getattr(cfg, "latitude", None) is None:
            continue
        feed_geo[fid] = svc.describe_snap(
            float(cfg.latitude), float(cfg.longitude),
            road_id=getattr(cfg, "road_id", None),
        )

    out = {}
    for r in roads:
        out[r.id] = {
            "road_name": r.name,
            "signals": signals_on_road(signals, r),
            "work_zones": workzones_on_road(zones, r.id, feed_geo),
        }
    n_sig = sum(len(v["signals"]) for v in out.values())
    n_wz = sum(len(v["work_zones"]) for v in out.values())
    return APIResponse.success(
        data={"roads": out},
        message=f"{len(out)} roads, {n_sig} signals, {n_wz} active work-zones.",
    )


@router.get(
    "/{road_id}",
    response_model=APIResponse[Road],
    summary="Get a Road",
)
async def get_road(
    road_id: str,
    svc: RoadService = Depends(get_road_service),
    current_user: User = Depends(get_current_viewer),
) -> APIResponse[Road]:
    road = await svc.get_road(road_id)
    if not road:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Road '{road_id}' not found.")
    return APIResponse.success(data=road, message="Road retrieved.")


@router.get(
    "/{road_id}/feeds",
    response_model=APIResponse[List[FeedStatusData]],
    summary="List feeds assigned to a road",
)
async def feeds_on_road(
    road_id: str,
    fm: FeedManager = Depends(get_feed_manager),
    svc: RoadService = Depends(get_road_service),
    current_user: User = Depends(get_current_viewer),
) -> APIResponse[List[FeedStatusData]]:
    if await svc.get_road(road_id) is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Road '{road_id}' not found.")
    statuses = await fm.get_all_statuses()
    assigned = [s for s in statuses if s.config and s.config.road_id == road_id]
    return APIResponse.success(
        data=assigned, message=f"{len(assigned)} feeds assigned to road '{road_id}'."
    )


@router.get(
    "/{road_id}/handoff",
    response_model=APIResponse[List[dict]],
    summary="Cross-camera handoff candidates on a road",
)
async def road_handoff(
    road_id: str,
    from_feed_id: str,
    motion_bearing_deg: float | None = None,
    fm: FeedManager = Depends(get_feed_manager),
    svc: RoadService = Depends(get_road_service),
    current_user: User = Depends(get_current_viewer),
) -> APIResponse[List[dict]]:
    """Other placed cameras on this road, ranked by distance ahead of
    from_feed_id in the direction of travel (motion_bearing_deg)."""
    road = await svc.get_road(road_id)
    if road is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Road '{road_id}' not found.")
    from app.utils.handoff import handoff_candidates
    statuses = await fm.get_all_statuses()
    cams = [
        {
            "feed_id": s.feed_id,
            "name": (s.config.name if s.config else None) or s.feed_id,
            "latitude": (s.config.latitude if s.config else None) or s.latitude,
            "longitude": (s.config.longitude if s.config else None) or s.longitude,
            "heading_deg": s.config.heading_deg if s.config else None,
            "road_id": s.config.road_id if s.config else None,
        }
        for s in statuses
    ]
    candidates = handoff_candidates(road, from_feed_id, motion_bearing_deg, cams)
    return APIResponse.success(
        data=candidates,
        message=f"{len(candidates)} handoff candidates ahead of '{from_feed_id}' on '{road_id}'.",
    )


@router.patch(
    "/{road_id}",
    response_model=APIResponse[Road],
    summary="Update a Road",
)
async def update_road(
    road_id: str,
    request: RoadUpdate,
    svc: RoadService = Depends(get_road_service),
    current_user: User = Depends(get_current_admin),
) -> APIResponse[Road]:
    road = await svc.update_road(road_id, request)
    if not road:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Road '{road_id}' not found.")
    return APIResponse.success(data=road, message=f"Road '{road_id}' updated.")


@router.delete(
    "/{road_id}",
    response_model=APIResponse[dict],
    summary="Delete a Road",
)
async def delete_road(
    road_id: str,
    svc: RoadService = Depends(get_road_service),
    current_user: User = Depends(get_current_admin),
) -> APIResponse[dict]:
    deleted = await svc.delete_road(road_id)
    if not deleted:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Road '{road_id}' not found.")
    return APIResponse.success(data={"deleted": road_id}, message=f"Road '{road_id}' deleted.")
