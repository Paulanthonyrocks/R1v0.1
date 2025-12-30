from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from app.database import get_database_manager
from app.utils.database import DatabaseManager
from pydantic import BaseModel
from datetime import datetime

router = APIRouter(prefix="/vehicles", tags=["Vehicles"])

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

@router.get("/", response_model=List[IdentifiedVehicleResponse])
async def list_identified_vehicles(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: DatabaseManager = Depends(get_database_manager)
):
    """
    Returns a list of identified vehicles (by license plate).
    """
    vehicles = await db.get_identified_vehicles(limit=limit, offset=offset)
    return vehicles

@router.get("/{license_plate}", response_model=IdentifiedVehicleResponse)
async def get_vehicle(
    license_plate: str,
    db: DatabaseManager = Depends(get_database_manager)
):
    """
    Returns details for a specific vehicle identified by license plate.
    """
    vehicle = await db.get_vehicle_by_plate(license_plate)
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return vehicle
