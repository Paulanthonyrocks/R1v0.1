from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional
from app.models.incidents import Incident, IncidentCreate, IncidentUpdate, IncidentStatus
from app.services.services import get_incident_manager, get_database_manager
from app.services.incident_manager import IncidentManager
from app.dependency_injection import get_current_active_user, get_current_admin
from app.models.user import User
from datetime import datetime, timezone

router = APIRouter(tags=["Incidents"])

@router.get("/", response_model=List[Incident])
async def list_incidents(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: Optional[IncidentStatus] = None,
    db = Depends(get_database_manager),
    current_user: User = Depends(get_current_active_user),
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
    manager: IncidentManager = Depends(get_incident_manager),
    current_user: User = Depends(get_current_admin),
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
    manager: IncidentManager = Depends(get_incident_manager),
    current_user: User = Depends(get_current_admin),
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
    manager: IncidentManager = Depends(get_incident_manager),
    current_user: User = Depends(get_current_admin),
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
    manager: IncidentManager = Depends(get_incident_manager),
    current_user: User = Depends(get_current_admin),
):
    """Update an incident's status or details."""
    existing = await manager._db_manager.get_incident_by_id(incident_id)
    if not existing:
        raise HTTPException(status_code=404, detail="Incident not found")
        
    update_data = update.model_dump(exclude_unset=True)
    if not update_data:
        return existing
        
    update_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    success = await manager._db_manager.update_incident(incident_id, update_data)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update incident")

    # Fetch updated record
    updated_record = await manager._db_manager.get_incident_by_id(incident_id)
    return updated_record


@router.get("/search/results", summary="Forensic search (feature 5)")
async def search_incidents(
    type: Optional[str] = None,
    severity: Optional[str] = None,
    feed_id: Optional[str] = None,
    lane: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    db = Depends(get_database_manager),
    current_user: User = Depends(get_current_active_user),
):
    """Attribute filter over incidents. Empty list when forensic disabled."""
    from app.services.forensic_service import ForensicService, filter_incidents
    from app.config import get_current_config

    svc = ForensicService(config=get_current_config().model_dump())
    if not svc.enabled:
        return []
    filters = {k: v for k, v in
               {"type": type, "severity": severity, "feed_id": feed_id,
                "lane": lane, "q": q}.items() if v is not None}
    incidents = await db.get_incidents(limit=min(limit * 5, 1000), offset=0, filters={})
    return filter_incidents(incidents, filters, limit)


@router.get("/{incident_id}/evidence", summary="Evidence bundle (feature 1)")
async def get_incident_evidence(
    incident_id: str,
    db = Depends(get_database_manager),
    current_user: User = Depends(get_current_active_user),
):
    """Manifest + snapshot list for an incident. 404 when no bundle yet."""
    from app.services.evidence_service import EvidenceService
    from app.config import get_current_config

    incident = await db.get_incident_by_id(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    svc = EvidenceService(config=get_current_config().model_dump())
    if not svc.enabled:
        raise HTTPException(status_code=501, detail="Evidence bundles disabled")
    bundle = svc.get_bundle(incident_id)
    if bundle is None:
        # No bundle minted yet: return live manifest without claiming a clip.
        from app.services.evidence_service import build_manifest
        return build_manifest(incident, [], None)
    return bundle


@router.get("/{incident_id}/escalation", summary="Escalation state (feature 2)")
async def get_incident_escalation(
    incident_id: str,
    db = Depends(get_database_manager),
    current_user: User = Depends(get_current_active_user),
):
    """OK/DUE/OVERDUE/ACKED computed from created/ack timestamps + config."""
    import time
    from app.services.escalation_service import EscalationService
    from app.config import get_current_config

    incident = await db.get_incident_by_id(incident_id)
    if not incident:
        raise HTTPException(status_code=404, detail="Incident not found")
    svc = EscalationService(config=get_current_config().model_dump())
    created = incident.get("timestamp") or incident.get("created_at") or time.time()
    try:
        created_ts = float(created)
    except (TypeError, ValueError):
        created_ts = time.time()
    ack_ts = incident.get("acknowledged_at")
    try:
        ack_ts = float(ack_ts) if ack_ts is not None else None
    except (TypeError, ValueError):
        ack_ts = None
    now = time.time()
    return {"incident_id": incident_id,
            "state": svc.state(created_ts, ack_ts, now),
            "level": svc.level(created_ts, now)}