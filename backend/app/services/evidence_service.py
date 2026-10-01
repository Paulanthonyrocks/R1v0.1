"""Incident evidence bundle (feature 1).

Collects the incident record + snapshot JPEGs + optional clip path into a
single on-disk bundle with a chain-of-custody manifest. Since Phase 1 the
manifest is cryptographically sealed: per-artefact SHA-256 digests plus a
signed manifest digest (see evidence_integrity).

Clip cutting needs ffmpeg/video recordings; when no clip is available the
manifest records clip_unavailable honestly instead of inventing one.

Sealing is FAIL-CLOSED. A bundle that cannot be sealed is still written (so
an incident is never silently lost), but it is marked `sealed: false`, and
verifying it fails loudly. Unsealed is never presented as verified.
"""
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.services import evidence_integrity as integrity

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
        # seal: True (default) = fail closed, i.e. log an error when key
        # material is missing. False = seal only when a key happens to exist.
        self.seal_enabled = cfg.get("seal", True)
        self.key_id = cfg.get("key_id", "dev-local")
        if self.enabled:
            try:
                self.evidence_dir.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                logger.warning(f"Evidence dir not writable {self.evidence_dir}: {e}")

    def bundle_path(self, incident_id: str) -> Path:
        safe = "".join(c for c in incident_id if c.isalnum() or c in "-_")
        return self.evidence_dir / safe

    def release_path(self, incident_id: str) -> Path:
        """Directory holding the masked, third-party release copy.

        Kept beside the evidence bundle (not inside it) so that releasing a
        masked copy can never mutate -- and thereby invalidate the seal on --
        the original bundle. Single source of truth: the export router derives
        this rather than building the name itself.
        """
        return self.bundle_path(incident_id).parent / f"{self.bundle_path(incident_id).name}_release"

    def _signer(self):
        """Return a signer, or None when key material is unavailable."""
        try:
            return integrity.get_signer()
        except integrity.SignerUnavailable as e:
            if self.seal_enabled:
                logger.error(f"EVIDENCE BUNDLE WILL BE UNSEALED: {e}")
            else:
                logger.warning(f"Evidence sealing skipped: {e}")
            return None

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
        copied: List[str] = []
        try:
            dest.mkdir(parents=True, exist_ok=True)
            for src in manifest["snapshots"]:
                try:
                    p = Path(src)
                    if p.exists() and p.is_file():
                        shutil.copy2(p, dest / p.name)
                        copied.append(p.name)
                except Exception as e:
                    logger.warning(f"Evidence snapshot copy failed {src}: {e}")
            # snapshots are rewritten to bundle-relative names: an absolute
            # staging path is meaningless once the bundle is moved or handed to
            # counsel, and would break any later re-hash.
            manifest["snapshots"] = copied
            (dest / "incident.json").write_text(json.dumps(incident, default=str))
            (dest / "manifest.json").write_text(json.dumps(manifest, default=str))

            # Hash every artefact, then seal.
            digests = integrity.hash_artefacts(
                dest, copied + ["incident.json", "manifest.json"]
            )
            # manifest.json cannot carry its own digest: the digest is computed
            # over the manifest, so hashing the manifest inside its own
            # inventory is self-referential and unverifiable. It is excluded
            # from the inventory, and the signed manifest digest is what
            # protects it.
            inventory = [d for d in digests if d["name"] != "manifest.json"]

            signer = self._signer()
            if signer is not None:
                # These flags must be set BEFORE sealing: the digest is computed
                # over the whole manifest, so anything added afterwards would
                # invalidate the seal and the bundle would fail its own verify.
                manifest["sealed"] = True
                manifest["key_id"] = self.key_id
                manifest["manifest_self_hash_excluded"] = True
                manifest = integrity.seal_manifest(manifest, signer, inventory)
            else:
                manifest["artefacts"] = inventory
                manifest["sealed"] = False

            (dest / "manifest.json").write_text(json.dumps(manifest, default=str))
            manifest["bundle_dir"] = str(dest)
            return manifest
        except Exception as e:
            logger.error(f"Evidence bundle save failed {incident_id}: {e}")
            return None

    def _expected_snapshot_names(self, incident: Dict[str, Any]) -> List[str]:
        """Names the ingestion worker will use for this incident's snapshot.

        Mirrors ingestion_worker.save_snapshot_async's naming
        (`{feed_id}_{incident_id}{ext}`) so the bundle can be completed
        retroactively once the worker finishes writing. Both extensions are
        checked because the worker picks jpg or png from the decoded frame
        format, which is not knowable at incident-creation time.
        """
        feed_id = incident.get("source_feed_id") or incident.get("feed_id")
        incident_id = incident.get("incident_id") or incident.get("id")
        if not feed_id or not incident_id:
            return []
        return [f"{feed_id}_{incident_id}{ext}" for ext in (".jpg", ".png")]

    def find_snapshots_on_disk(
        self,
        incident: Dict[str, Any],
        snapshots_dir: Path,
    ) -> List[Path]:
        """Locate snapshot(s) already written for this incident."""
        found: List[Path] = []
        if not snapshots_dir.is_dir():
            return found
        for name in self._expected_snapshot_names(incident):
            p = snapshots_dir / name
            if p.is_file():
                found.append(p)
        return found

    def attach_snapshots(
        self,
        incident: Dict[str, Any],
        snapshot_paths: List[str],
        clip_path: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Add late-arriving snapshots to an existing bundle and RE-SEAL it.

        Snapshots are written asynchronously by the ingestion worker after the
        incident row exists, so the bundle is first minted with no imagery and
        completed here once the files land. The manifest is rebuilt and sealed
        again because the artefact inventory changed -- a bundle cannot be
        re-verified against a digest that predates its own contents.
        """
        if not self.enabled:
            return None
        incident_id = str(incident.get("incident_id") or incident.get("id") or "unknown")
        dest = self.bundle_path(incident_id)
        if not dest.is_dir():
            logger.debug(f"No bundle to attach snapshots to for {incident_id}")
            return None

        existing = self.get_bundle(incident_id) or build_manifest(incident, [], clip_path)
        already = set(existing.get("snapshots") or [])
        try:
            dest.mkdir(parents=True, exist_ok=True)
            names = list(already)
            for src in snapshot_paths:
                p = Path(src)
                if not (p.exists() and p.is_file()):
                    continue
                if p.name in already:
                    continue
                shutil.copy2(p, dest / p.name)
                names.append(p.name)
            if names == list(already) and not (already and clip_path):
                return existing  # nothing new to attach

            existing["snapshots"] = sorted(set(names))
            existing["clip"] = clip_path or existing.get("clip")
            existing["clip_unavailable"] = existing["clip"] is None
            existing["completed_at"] = time.time()

            inventory = [
                d for d in integrity.hash_artefacts(
                    dest, existing["snapshots"] + ["incident.json", "manifest.json"]
                ) if d["name"] != "manifest.json"
            ]
            signer = self._signer()
            if signer is not None:
                existing["sealed"] = True
                existing["key_id"] = self.key_id
                existing["manifest_self_hash_excluded"] = True
                existing = integrity.seal_manifest(existing, signer, inventory)
            else:
                existing["artefacts"] = inventory
                existing["sealed"] = False

            (dest / "manifest.json").write_text(json.dumps(existing, default=str))
            existing["bundle_dir"] = str(dest)
            logger.info(
                f"Evidence bundle {incident_id} completed with "
                f"{len(existing['snapshots'])} snapshot(s), sealed={existing['sealed']}"
            )
            return existing
        except Exception as e:
            logger.error(f"Evidence snapshot attach failed {incident_id}: {e}")
            return None

    def get_bundle(self, incident_id: str) -> Optional[Dict[str, Any]]:
        manifest_path = self.bundle_path(incident_id) / "manifest.json"
        if not manifest_path.exists():
            return None
        try:
            return json.loads(manifest_path.read_text())
        except Exception as e:
            logger.error(f"Evidence manifest unreadable {incident_id}: {e}")
            return None

    def seal_release(
        self,
        incident_id: str,
        masked_dir: Path,
        results: List[Dict[str, Any]],
        source_manifest: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Seal the masked release copy so the RELEASED artefact has custody.

        The evidence bundle's seal covers the unmasked originals. The images
        actually handed to a third party are different bytes produced by a
        different process, so an unsealed release means the artefact that
        leaves the building cannot be shown unaltered -- which is the one the
        recipient will actually be holding. This seals that copy too.

        The release manifest is a DISTINCT document from the evidence
        manifest. It records what masking did (`fully_masked`, per-artefact
        outcomes) rather than pretending to be the original bundle, and it
        carries the source bundle's digest so the chain from released copy
        back to sealed original is explicit and checkable.

        Fail-closed, exactly like save_bundle: when no key is available the
        release is still written (the caller already produced the files) but
        marked `sealed: false`, and verify_release refuses to call it valid.
        """
        if not self.enabled:
            return {"sealed": False, "reason": "evidence bundles disabled"}

        masked_count = sum(1 for r in results if r.get("masked"))
        total = len(results)
        release = {
            "incident_id": incident_id,
            "document": "masked_release",
            "released_at": time.time(),
            # Honest statement of what the privacy control actually did. A
            # release with fully_masked=false is still a valid, sealed
            # document -- it is a sealed record that the masking was partial.
            "privacy": {
                "enabled": True,
                "snapshots_total": total,
                "snapshots_masked": masked_count,
                "fully_masked": bool(total) and masked_count == total,
                "results": results,
            },
            # Names only, never absolute source paths: the release is meant to
            # be verifiable on another host, where our staging paths mean
            # nothing.
            "artefact_names": sorted(
                r["name"] for r in results if r.get("masked") and r.get("name")
            ),
        }

        # Chain back to the sealed original. Absent source manifest is
        # recorded as such rather than omitted, so the reader can tell an
        # unlinked release from one whose parent was never sealed.
        src_digest = (source_manifest or {}).get("integrity", {}).get("manifest_digest")
        release["derived_from_bundle_digest"] = src_digest
        release["source_bundle_sealed"] = bool(
            (source_manifest or {}).get("sealed")
        )

        try:
            masked_dir.mkdir(parents=True, exist_ok=True)
            inventory = integrity.hash_artefacts(masked_dir, release["artefact_names"])
            signer = self._signer()
            if signer is not None:
                # Set BEFORE sealing -- see save_bundle: the digest covers the
                # whole manifest, so a field added afterwards breaks verify.
                release["sealed"] = True
                release["key_id"] = self.key_id
                release["manifest_self_hash_excluded"] = True
                release = integrity.seal_manifest(release, signer, inventory)
            else:
                release["artefacts"] = inventory
                release["sealed"] = False

            (masked_dir / "manifest.json").write_text(json.dumps(release, default=str))
            release["release_dir"] = str(masked_dir)
            logger.info(
                f"Release bundle {incident_id} sealed={release['sealed']} "
                f"masked={masked_count}/{total}"
            )
            return release
        except Exception as e:
            # Never lose the release: the files exist and the caller must be
            # able to hand them over with an honest "not sealed" marker.
            logger.error(f"Release bundle seal failed {incident_id}: {e}")
            release["sealed"] = False
            release["seal_error"] = str(e)
            release["release_dir"] = str(masked_dir)
            try:
                (masked_dir / "manifest.json").write_text(
                    json.dumps(release, default=str)
                )
            except Exception:
                pass
            return release

    def verify_release(self, incident_id: str) -> Dict[str, Any]:
        """Verify a sealed masked release. Mirrors verify_bundle's contract.

        Never raises: a verification failure is a result, not an exception.
        """
        release_dir = self.release_path(incident_id)
        manifest_path = release_dir / "manifest.json"
        if not manifest_path.exists():
            return {"valid": False, "sealed": False,
                    "reason": f"no release produced for incident {incident_id}",
                    "incident_id": incident_id, "document": "masked_release",
                    "artefacts": []}
        try:
            manifest = json.loads(manifest_path.read_text())
        except Exception as e:
            return {"valid": False, "sealed": False,
                    "reason": f"release manifest unreadable: {e}",
                    "incident_id": incident_id, "document": "masked_release",
                    "artefacts": []}
        if not manifest.get("sealed"):
            return {"valid": False, "sealed": False,
                    "reason": "release was never sealed; it cannot be shown unaltered",
                    "incident_id": incident_id, "document": "masked_release",
                    "artefacts": []}
        try:
            signer = integrity.get_signer()
        except integrity.SignerUnavailable as e:
            return {"valid": False, "sealed": True,
                    "reason": f"cannot verify: {e}",
                    "incident_id": incident_id, "document": "masked_release",
                    "artefacts": []}
        report = integrity.verify_manifest(manifest, release_dir, signer)
        report["incident_id"] = incident_id
        report["sealed"] = True
        report["key_id"] = manifest.get("key_id")
        report["document"] = "masked_release"
        report["privacy"] = manifest.get("privacy")
        report["derived_from_bundle_digest"] = manifest.get("derived_from_bundle_digest")
        return report

    def verify_bundle(self, incident_id: str) -> Dict[str, Any]:
        """Verify a sealed bundle end to end.

        Returns a report suitable for showing to counsel or an auditor. Never
        raises: a verification failure is a result, not an exception.
        """
        manifest = self.get_bundle(incident_id)
        if manifest is None:
            return {"valid": False, "sealed": False,
                    "reason": f"no bundle for incident {incident_id}",
                    "incident_id": incident_id, "artefacts": []}
        if not manifest.get("sealed"):
            return {"valid": False, "sealed": False,
                    "reason": "bundle was never sealed; it cannot be shown unaltered",
                    "incident_id": incident_id, "artefacts": []}
        try:
            signer = integrity.get_signer()
        except integrity.SignerUnavailable as e:
            return {"valid": False, "sealed": True,
                    "reason": f"cannot verify: {e}",
                    "incident_id": incident_id, "artefacts": []}
        report = integrity.verify_manifest(manifest, self.bundle_path(incident_id), signer)
        report["incident_id"] = incident_id
        report["sealed"] = True
        report["key_id"] = manifest.get("key_id")
        return report
