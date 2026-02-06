from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from app.models.incidents import Incident, IncidentCreate, IncidentUpdate, IncidentStatus
from app.services.services import get_incident_manager, get_database_manager
from app.services.incident_manager import IncidentManager
from datetime import datetime, timezone

router = APIRouter(tags=["Incidents"])

@router.get("/", response_model=List[Incident])
async def list_incidents(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: Optional[IncidentStatus] = None,
    db = Depends(get_database_manager)
):
    """List recent incidents with optional filtering."""
    filters = {}
    if status:
        filters["status"] = status.value
        
    incidents = await db.get_incidents(limit=limit, offset=offset, filters=filters)
    return incidents

@router.post("/", response_model=Incident)
async def create_new_incident(
    incident: IncidentCreate,
    manager: IncidentManager = Depends(get_incident_manager)
):
    """Manually report a new incident."""
    incident_id = await manager.create_incident(
        location={"latitude": incident.latitude, "longitude": incident.longitude},
        incident_type=incident.type,
        severity=incident.severity,
        description=incident.description,
        source_feed_id=incident.feed_id,
        bypass_debounce=True
    )
    
    if not incident_id:
        raise HTTPException(status_code=500, detail="Failed to create incident")
        
    return await manager._db_manager.get_incident_by_id(incident_id)

@router.post("/{incident_id}/acknowledge", response_model=Incident)
async def acknowledge_incident(
    incident_id: str,
    user_id: str = Query("operator", alias="user_id"),
    manager: IncidentManager = Depends(get_incident_manager)
):
    """Acknowledge an incident."""
    success = await manager.update_status(
        incident_id, IncidentStatus.ACKNOWLEDGED, user_id=user_id
    )
    if not success:
        raise HTTPException(status_code=400, detail="Failed to acknowledge incident")
    return await manager._db_manager.get_incident_by_id(incident_id)

@router.post("/{incident_id}/resolve", response_model=Incident)
async def resolve_incident(
    incident_id: str,
    notes: Optional[str] = Query(None),
    user_id: str = Query("operator", alias="user_id"),
    manager: IncidentManager = Depends(get_incident_manager)
):
    """Resolve an incident."""
    success = await manager.update_status(
        incident_id, IncidentStatus.RESOLVED, user_id=user_id, notes=notes
    )
    if not success:
        raise HTTPException(status_code=400, detail="Failed to resolve incident")
    return await manager._db_manager.get_incident_by_id(incident_id)

@router.patch("/{incident_id}", response_model=Incident)
async def update_incident(
    incident_id: str,
    update: IncidentUpdate,
    manager: IncidentManager = Depends(get_incident_manager)
):
    """Update an incident's status or details."""
    existing = await manager._db_manager.get_incident_by_id(incident_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Incident not found")
        
    update_data = update.model_dump(exclude_unset=True)
    if not update_data:
        return existing
        
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    success = await db.update_incident(incident_id, update_data)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update incident")
        
    # Fetch updated record
    updated_record = await db.get_incident_by_id(incident_id)
    return updated_record