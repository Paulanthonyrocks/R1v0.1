"""Incident evidence bundle (feature 1).

Collects the incident record + snapshot JPEGs + optional clip path into a
single on-disk bundle with a manifest.json chain-of-custody stub.
Clip cutting needs ffmpeg/video recordings; when no clip is available the
manifest records clip_unavailable honestly instead of inventing one.
"""
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("app.services.evidence")


def build_manifest(
    incident: Dict[str, Any],
    snapshot_paths: List[str],
    clip_path: Optional[str] = None,
) -> Dict[str, Any]:
    manifest = {
        "incident_id": incident.get("incident_id") or incident.get("id"),
        "type": incident.get("type"),
        "severity": incident.get("severity"),
        "feed_id": incident.get("source_feed_id") or incident.get("feed_id"),
        "timestamp": incident.get("timestamp"),
        "snapshots": list(snapshot_paths),
        "clip": clip_path,
        "clip_unavailable": clip_path is None,
        "bundled_at": time.time(),
    }
    return manifest


class EvidenceService:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        cfg = config.get("evidence", {})
        self.enabled = cfg.get("enabled", True)
        self.evidence_dir = Path(cfg.get("dir", "backend/data/evidence"))
        if self.enabled:
            try:
                self.evidence_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning(f"Evidence dir not writable {self.evidence_dir}: {e}")

    def bundle_path(self, incident_id: str) -> Path:
        safe = "".join(c for c in incident_id if c.isalnum() or c in "-_")
        return self.evidence_dir / safe

    def save_bundle(
        self,
        incident: Dict[str, Any],
        snapshot_paths: Optional[List[str]] = None,
        clip_path: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.enabled:
            return None
        incident_id = str(incident.get("incident_id") or incident.get("id") or "unknown")
        manifest = build_manifest(incident, snapshot_paths or [], clip_path)
        dest = self.bundle_path(incident_id)
        try:
            dest.mkdir(parents=True, exist_ok=True)
            for src in manifest["snapshots"]:
                try:
                    p = Path(src)
                    if p.exists() and p.is_file():
                        shutil.copy2(p, dest / p.name)
                except Exception as e:
                    logger.warning(f"Evidence snapshot copy failed {src}: {e}")
            (dest / "incident.json").write_text(json.dumps(incident, default=str))
            (dest / "manifest.json").write_text(json.dumps(manifest, default=str))
            manifest["bundle_dir"] = str(dest)
            return manifest
        except Exception as e:
            logger.error(f"Evidence bundle save failed {incident_id}: {e}")
            return None

    def get_bundle(self, incident_id: str) -> Optional[Dict[str, Any]]:
        manifest_path = self.bundle_path(incident_id) / "manifest.json"
        if not manifest_path.exists():
            return None
        try:
            return json.loads(manifest_path.read_text())
        except Exception as e:
            logger.warning(f"Evidence manifest unreadable {incident_id}: {e}")
            return None
