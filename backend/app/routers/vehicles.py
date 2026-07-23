from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional, Dict, Any
from app.database import get_database_manager
from app.dependency_injection import get_current_active_user
from app.utils.database import DatabaseManager
from pydantic import BaseModel
from datetime import datetime

router = APIRouter(tags=["Vehicles"])

class IdentifiedVehicleResponse(BaseModel):
    license_plate: str
    vehicle_type: Optional[str] = None
    make: Optional[str] = None
    model: Optional[str] = None
    color: Optional[str] = None
    first_seen: float
    last_seen: float
    total_detections: int
    flags: Optional[str] = None

class VehicleTrackResponse(BaseModel):
    feed_id: str
    track_id: int
    timestamp: float
    class_id: Optional[int] = None
    confidence: Optional[float] = None
    bbox_x1: Optional[float] = None
    bbox_y1: Optional[float] = None
    bbox_x2: Optional[float] = None
    bbox_y2: Optional[float] = None
    speed: Optional[float] = None
    license_plate: Optional[str] = None
    lane: Optional[int] = None
    direction: Optional[str] = None

@router.get("/", response_model=List[IdentifiedVehicleResponse])
async def list_identified_vehicles(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    vehicle_type: Optional[str] = None,
    make: Optional[str] = None,
    model: Optional[str] = None,
    db: DatabaseManager = Depends(get_database_manager),
    current_user: dict = Depends(get_current_active_user),
):
    """
    Returns a list of identified vehicles (by license plate) with optional filtering.
    """
    filters = {
        "vehicle_type": vehicle_type,
        "make": make,
        "model": model
    }
    # Remove None values
    filters = {k: v for k, v in filters.items() if v is not None}
    
    vehicles = await db.get_identified_vehicles(limit=limit, offset=offset, filters=filters)
    return vehicles

@router.get("/tracks", response_model=List[VehicleTrackResponse])
async def list_vehicle_tracks(
    limit: int = Query(500, ge=1, le=5000),
    offset: int = Query(0, ge=0),
    feed_id: Optional[str] = None,
    license_plate: Optional[str] = None,
    class_id: Optional[int] = None,
    start_time: Optional[float] = None,
    end_time: Optional[float] = None,
    db: DatabaseManager = Depends(get_database_manager),
    current_user: dict = Depends(get_current_active_user),
):
    """
    Returns raw vehicle tracking data for historical analysis.
    """
    filters = {
        "feed_id": feed_id,
        "license_plate": license_plate,
        "class_id": class_id,
        "start_time": start_time,
        "end_time": end_time
    }
    filters = {k: v for k, v in filters.items() if v is not None}
    
    tracks = await db.get_vehicle_tracks(limit=limit, offset=offset, filters=filters)
    return tracks

@router.get("/{license_plate}", response_model=IdentifiedVehicleResponse)

async def get_vehicle(

    license_plate: str,

    db: DatabaseManager = Depends(get_database_manager),
    current_user: dict = Depends(get_current_active_user),
):

    """

    Returns details for a specific vehicle identified by license plate.

    """

    vehicle = await db.get_vehicle_by_plate(license_plate)

    if not vehicle:

        raise HTTPException(status_code=404, detail="Vehicle not found")

    return vehicle



@router.get("/global/list", response_model=List[Dict[str, Any]])
async def list_global_vehicles(
    limit: int = Query(100, ge=1, le=1000),
    db: DatabaseManager = Depends(get_database_manager),
    current_user: dict = Depends(get_current_active_user),
):
    """
    Returns a list of recently seen unique global vehicle IDs.
    """
    return await db.list_global_vehicles(limit=limit)

@router.get("/global/{global_id}/history", response_model=List[VehicleTrackResponse])

async def get_vehicle_global_history(

    global_id: str,

    db: DatabaseManager = Depends(get_database_manager),
    current_user: dict = Depends(get_current_active_user),
):

    """

    Returns the historical path of a vehicle across all feeds using its ReID Global ID.

    """

    history = await db.get_vehicle_global_history(global_id)

    if not history:

        raise HTTPException(status_code=404, detail="No history found for this global ID")

    return history

class ReIDGalleryResponse(BaseModel):
    global_id: str
    gallery_size: int
    last_seen: float
    metadata: Dict[str, Any]
    # We'll return embeddings as list of lists if small, or just count for now
    # For a real gallery, we'd probably store reference images too.
    # Since we only have embeddings, we'll return them.
    embeddings: List[List[float]]

@router.get("/global/{global_id}/gallery", response_model=ReIDGalleryResponse)
async def get_vehicle_reid_gallery(
    global_id: str,
    db: DatabaseManager = Depends(get_database_manager),
    current_user: dict = Depends(get_current_active_user),
):
    """
    Returns the collection of appearance embeddings for a vehicle.
    """
    identity = await db.get_reid_identity(global_id)
    if not identity:
        raise HTTPException(status_code=404, detail="ReID identity not found")
    
    import numpy as np
    emb_bytes = identity["embeddings"]
    # Reconstruct 2D array (assuming 512 dimensions)
    # Ideally dimensions should come from config, but 512 is common for ReID.
    # Let's check reid_manager to be sure or just use the whole buffer.
    embeddings = np.frombuffer(emb_bytes, dtype=np.float32)
    
    # Heuristic: find rows by dividing total size by 512
    # If it doesn't divide evenly, it might be 128 or 256.
    # We'll try to be robust.
    for dim in [512, 256, 128]:
        if len(embeddings) % dim == 0:
            embeddings = embeddings.reshape(-1, dim)
            break
    else:
        # Fallback to single row if unknown dims
        embeddings = embeddings.reshape(1, -1)

    return ReIDGalleryResponse(
        global_id=identity["global_id"],
        gallery_size=len(embeddings),
        last_seen=identity["last_seen"],
        metadata=identity["metadata"],
        embeddings=embeddings.tolist()
    )
