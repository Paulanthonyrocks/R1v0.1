import asyncio
import logging
import time
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Any, Dict, List, Optional
from app.models.incidents import Incident, IncidentCreate, IncidentUpdate, IncidentStatus
from app.services.services import get_incident_manager, get_database_manager
from app.services.incident_manager import IncidentManager
from app.dependency_injection import get_current_active_user, get_current_admin
from app.models.user import User
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

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
    # Reading law-enforcement evidence is itself a disclosable access.
    await _audit_evidence_access(
        incident_id, "EVIDENCE_READ",
        f"snapshots={len(bundle.get('snapshots') or [])} "
        f"sealed={bundle.get('sealed')}",
        current_user,
    )
    return bundle


async def _audit_evidence_access(
    incident_id: str, action: str, details: str, current_user: User
) -> None:
    """Record an evidence access. Never raises: an audit failure must not
    block the read itself, but is logged loudly."""
    try:
        db = get_database_manager()
        sql = (
            "INSERT INTO audit_log (user_id, action, resource_type, resource_id, "
            "details, ip_address, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)"
        )
        await asyncio.to_thread(db._execute_write, sql, (
            getattr(current_user, "username", "unknown"),
            action, "INCIDENT", incident_id, details, None, time.time(),
        ))
    except Exception as e:
        logger.warning(f"Evidence access audit write failed ({action}): {e}")


@router.get("/{incident_id}/evidence/export", summary="Export evidence for release")
async def export_incident_evidence(
    incident_id: str,
    current_user: User = Depends(get_current_admin),
):
    """Produce a release copy of the bundle with plate/face regions masked.

    Admin-only and audit-logged. The response reports what was actually done:
    an unmasked release is reported as unmasked, with the reason, rather than
    asserting a privacy control that did not run.
    """
    from app.services.evidence_service import EvidenceService
    from app.services.privacy_service import PrivacyService
    from app.config import get_current_config

    cfg = get_current_config().model_dump()
    svc = EvidenceService(config=cfg)
    if not svc.enabled:
        raise HTTPException(status_code=501, detail="Evidence bundles disabled")

    bundle = svc.get_bundle(incident_id)
    if bundle is None:
        raise HTTPException(status_code=404, detail="No evidence bundle for incident")

    priv = PrivacyService(config=cfg)
    bundle_dir = svc.bundle_path(incident_id)
    masked_dir = bundle_dir.parent / f"{bundle_dir.name}_release"
    results: List[Dict[str, Any]] = []
    masked_count = 0

    for name in bundle.get("snapshots") or []:
        src = bundle_dir / name
        if not src.is_file():
            results.append({"name": name, "masked": False, "reason": "missing"})
            continue
        report = priv.mask_evidence_snapshot(src, masked_dir / name)
        if report.get("masked"):
            masked_count += 1
        results.append({"name": name, **report})

    await _audit_evidence_access(
        incident_id, "EVIDENCE_EXPORT",
        f"snapshots={len(results)} masked={masked_count} "
        f"privacy_enabled={priv.enabled}",
        current_user,
    )

    return {
        "incident_id": incident_id,
        "release_dir": str(masked_dir),
        "privacy_enabled": priv.enabled,
        "snapshots_total": len(results),
        "snapshots_masked": masked_count,
        # True only when every release image was actually masked.
        "fully_masked": bool(results) and masked_count == len(results),
        "results": results,
    }


@router.get("/{incident_id}/evidence/verify", summary="Verify evidence bundle integrity")
async def verify_incident_evidence(
    incident_id: str,
    current_user: User = Depends(get_current_admin),
):
    """Independently verify a sealed evidence bundle.

    Admin-only: verification is an evidentiary act and every invocation is
    audit-logged with the caller's identity. Returns a report, never a bare
    boolean, so an assessor can see WHICH artefact failed.
    """
    from app.services.evidence_service import EvidenceService
    from app.database import get_database_manager
    from app.config import get_current_config

    svc = EvidenceService(config=get_current_config().model_dump())
    if not svc.enabled:
        raise HTTPException(status_code=501, detail="Evidence bundles disabled")

    report = svc.verify_bundle(incident_id)

    # Audit the access itself, whatever the outcome. A failed verification is
    # exactly the event a reviewer needs to see.
    try:
        db = get_database_manager()
        if db is not None:
            sql = (
                "INSERT INTO audit_log (user_id, action, resource_type, resource_id, "
                "details, ip_address, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)"
            )
            await asyncio.to_thread(db._execute_write, sql, (
                getattr(current_user, "username", "unknown"),
                "EVIDENCE_VERIFY",
                "INCIDENT",
                incident_id,
                f"valid={report.get('valid')} reason={report.get('reason')}",
                None,
                time.time(),
            ))
    except Exception as e:  # audit must not mask the verification result
        logger.warning(f"Evidence verify audit write failed: {e}")

    return report


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