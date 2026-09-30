"""Evidence bundle integrity primitives (Phase 1).

Supplies the cryptographic seal that the evidence manifest previously lacked:
a per-artefact SHA-256 digest, a canonical-manifest digest, and an HMAC-SHA256
signature over that digest.

SIGNATURE SCHEME LIMITATION -- read before relying on this for court.
The current signer is HMAC-SHA256 (stdlib; no `cryptography` dependency is
available in this deployment). HMAC proves integrity and authenticity, but it
is a SYMMETRIC construction: any party holding the key can both produce and
verify signatures. It therefore does NOT give non-repudiation, and it is NOT
a digital signature in the legal sense. A bundle signed only with HMAC can be
shown to be unaltered relative to the key, but it cannot be shown to have been
signed by the State rather than by Route One.

`Signer` is an interface precisely so this can be replaced. Deploying
Ed25519 (via the `cryptography` package) or a KMS-held asymmetric key
requires no change to EvidenceService, the manifest shape, or the
verification endpoint. Until then, `manifest["integrity"]["scheme"]` reports
`hmac-sha256` and must be surfaced honestly to any assessor.
"""
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCHEME = "hmac-sha256"
CHUNK = 1024 * 1024

# Fields excluded from the signed digest. These are written after signing
# (or describe the seal itself), so including them would make the digest
# self-referential and impossible to reproduce.
_EXCLUDED_FROM_DIGEST = {"integrity"}


# --------------------------------------------------------------------------
# Hashing
# --------------------------------------------------------------------------

def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> Optional[str]:
    """Streamed SHA-256 of a file. Returns None if unreadable -- never a fake
    digest, because a missing artefact must not verify."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while True:
                block = fh.read(CHUNK)
                if not block:
                    break
                h.update(block)
    except OSError:
        return None
    return h.hexdigest()


def canonical_json(payload: Any) -> bytes:
    """Byte-stable JSON: sorted keys, no incidental whitespace, UTF-8.

    The digest is only reproducible if serialisation is deterministic, so
    every sign and every verify must go through this function.
    """
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        default=str,
    ).encode("utf-8")


def compute_manifest_digest(manifest: Dict[str, Any]) -> str:
    """SHA-256 over the canonical manifest minus the integrity block."""
    body = {k: v for k, v in manifest.items() if k not in _EXCLUDED_FROM_DIGEST}
    return sha256_bytes(canonical_json(body))


# --------------------------------------------------------------------------
# Signing
# --------------------------------------------------------------------------

class Signer:
    """Interface for manifest signing.

    Implement `sign`/`verify` over a raw payload. Swap in an asymmetric or
    KMS-backed implementation without touching any caller.
    """

    scheme = SCHEME

    def sign(self, payload: bytes) -> str:
        raise NotImplementedError

    def verify(self, payload: bytes, signature: str) -> bool:
        raise NotImplementedError


class HmacSigner(Signer):
    """Symmetric HMAC-SHA256. See module docstring on non-repudiation."""

    scheme = SCHEME

    def __init__(self, key: bytes):
        if not key:
            raise ValueError("HMAC key is empty; refusing to sign")
        self._key = key

    def sign(self, payload: bytes) -> str:
        return hmac.new(self._key, payload, hashlib.sha256).hexdigest()

    def verify(self, payload: bytes, signature: str) -> bool:
        if not signature:
            return False
        return hmac.compare_digest(self.sign(payload), signature)


class SignerUnavailable(RuntimeError):
    """Raised when a seal is required but no key material is configured.

    Deliberately distinct from a signing failure: an absent key must fail
    loudly and visibly, not silently produce unsealed bundles.
    """


def load_key(explicit: Optional[str] = None) -> bytes:
    """Resolve the signing key, in precedence order:
    1. explicit argument
    2. ROUTE_ONE_EVIDENCE_KEY env var (hex or utf-8)
    3. ROUTE_ONE_EVIDENCE_KEY_FILE env var (raw bytes)
    Raises SignerUnavailable when none is set.
    """
    if explicit:
        return explicit.encode("utf-8") if isinstance(explicit, str) else explicit

    inline = os.getenv("ROUTE_ONE_EVIDENCE_KEY")
    if inline:
        return inline.encode("utf-8")

    key_file = os.getenv("ROUTE_ONE_EVIDENCE_KEY_FILE")
    if key_file:
        try:
            data = Path(key_file).read_bytes()
        except OSError as e:
            raise SignerUnavailable(f"Key file unreadable: {key_file}") from e
        if not data:
            raise SignerUnavailable(f"Key file empty: {key_file}")
        return data

    raise SignerUnavailable(
        "No evidence signing key configured. Set ROUTE_ONE_EVIDENCE_KEY or "
        "ROUTE_ONE_EVIDENCE_KEY_FILE. Unsealed bundles are not evidence."
    )


def get_signer(explicit: Optional[str] = None) -> HmacSigner:
    return HmacSigner(load_key(explicit))


# --------------------------------------------------------------------------
# Seal / verify
# --------------------------------------------------------------------------

def seal_manifest(
    manifest: Dict[str, Any],
    signer: Signer,
    artefact_digests: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Attach the integrity block. Mutates and returns `manifest`.

    `artefact_digests` is the per-artefact list from hash_artefacts(). It is
    written into the manifest BEFORE the digest is computed so that the digest
    covers the artefact inventory itself -- otherwise the inventory could be
    edited after signing.
    """
    manifest = dict(manifest)
    manifest["artefacts"] = artefact_digests
    digest = compute_manifest_digest(manifest)
    manifest["integrity"] = {
        "scheme": signer.scheme,
        "manifest_digest": digest,
        "signature": signer.sign(canonical_json({"manifest_digest": digest})),
        "sealed_at": time.time(),
    }
    return manifest


def hash_artefacts(
    bundle_dir: Path,
    names: List[str],
) -> List[Dict[str, Any]]:
    """Digest every named artefact present in the bundle.

    Absent files are reported with `present: false` and a null digest rather
    than being skipped, so the inventory is a complete account of what the
    bundle claims to contain.
    """
    out: List[Dict[str, Any]] = []
    for name in sorted(names):
        p = bundle_dir / name
        present = p.is_file()
        out.append({
            "name": name,
            "present": present,
            "sha256": sha256_file(p) if present else None,
            "size_bytes": p.stat().st_size if present else None,
        })
    return out


def verify_manifest(
    manifest: Dict[str, Any],
    bundle_dir: Optional[Path],
    signer: Signer,
) -> Dict[str, Any]:
    """Full verification: signature, manifest digest, and per-artefact hashes.

    Returns a report rather than a bool -- counsel needs to know WHICH
    artefact failed, not merely that something did.
    """
    integrity = manifest.get("integrity")
    if not integrity:
        return {"valid": False, "reason": "manifest is not sealed",
                "artefacts": []}

    scheme = integrity.get("scheme")
    if scheme != signer.scheme:
        return {"valid": False,
                "reason": f"unsupported signature scheme: {scheme!r}",
                "artefacts": []}

    claimed_digest = integrity.get("manifest_digest")
    if not claimed_digest:
        return {"valid": False, "reason": "manifest carries no digest",
                "artefacts": []}

    # 1. Manifest not edited after sealing.
    recomputed = compute_manifest_digest(manifest)
    manifest_ok = hmac.compare_digest(recomputed, claimed_digest)

    # 2. Signature authenticates the digest.
    sig_ok = signer.verify(
        canonical_json({"manifest_digest": claimed_digest}),
        integrity.get("signature") or "",
    )

    # 3. Every artefact still hashes to what the signed manifest says.
    artefact_results: List[Dict[str, Any]] = []
    artefacts_ok = True
    for entry in manifest.get("artefacts") or []:
        name = entry.get("name")
        expected = entry.get("sha256")
        if not bundle_dir:
            artefact_results.append({
                "name": name, "ok": None,
                "note": "artefact not checked: no bundle directory supplied",
            })
            continue
        actual = sha256_file(bundle_dir / name) if (bundle_dir / name).is_file() else None
        ok = actual is not None and expected is not None and hmac.compare_digest(actual, expected)
        if not ok:
            artefacts_ok = False
        artefact_results.append({
            "name": name,
            "ok": ok,
            "present": actual is not None,
            "expected_sha256": expected,
            "actual_sha256": actual,
        })

    valid = manifest_ok and sig_ok and artefacts_ok
    reasons: List[str] = []
    if not manifest_ok:
        reasons.append("manifest contents were modified after sealing")
    if not sig_ok:
        reasons.append("signature does not authenticate the manifest digest")
    if not artefacts_ok:
        reasons.append("one or more artefacts are missing or altered")

    return {
        "valid": valid,
        "reason": "; ".join(reasons) if reasons else None,
        "signature_valid": sig_ok,
        "manifest_digest_valid": manifest_ok,
        "artefacts_valid": artefacts_ok,
        "scheme": scheme,
        "non_repudiation": False,  # HMAC is symmetric. See module docstring.
        "artefacts": artefact_results,
        "verified_at": time.time(),
    }
