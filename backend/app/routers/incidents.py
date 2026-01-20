from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from app.database import get_database_manager
from app.utils.database import DatabaseManager
from app.models.incidents import Incident, IncidentCreate, IncidentUpdate, IncidentStatus
from datetime import datetime, timezone
import uuid
import time

router = APIRouter(tags=["Incidents"])

@router.get("/", response_model=List[Incident])
async def list_incidents(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: Optional[IncidentStatus] = None,
    db: DatabaseManager = Depends(get_database_manager)
):
    """List recent incidents with optional filtering."""
    filters = {}
    if status:
        filters["status"] = status.value
        
    incidents = await db.get_incidents(limit=limit, offset=offset, filters=filters)
    return incidents

@router.post("/", response_model=Incident)
async def create_incident(
    incident: IncidentCreate,
    db: DatabaseManager = Depends(get_database_manager)
):
    """Manually report a new incident."""
    new_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    
    new_incident = Incident(
        id=new_id,
        timestamp=time.time(),
        created_at=now,
        updated_at=now,
        status=IncidentStatus.NEW,
        **incident.model_dump()
    )
    
    success = await db.create_incident(new_incident.model_dump())
    if not success:
        raise HTTPException(status_code=500, detail="Failed to create incident")
        
    return new_incident

@router.patch("/{incident_id}", response_model=Incident)
async def update_incident(
    incident_id: str,
    update: IncidentUpdate,
    db: DatabaseManager = Depends(get_database_manager)
):
    """Update an incident's status or details."""
    existing = await db.get_incident_by_id(incident_id)
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