"""Create the evidence signing key file (Method B).

Run once per engagement, in the same shell that will start the backend:

    python -m app.utils.make_evidence_key

Writes backend/keys/evidence.key with mode 600 and prints the export line.
Refuses to overwrite an existing key, because rotating invalidates every
bundle already sealed with the old one.

Key files are gitignored (.gitignore: backend/keys/, *.key).

Run it as a FILE, not as a module:

    python backend/app/utils/make_evidence_key.py

`python -m app.utils.make_evidence_key` executes app/utils/__init__.py, which
imports yaml and the rest of the app -- so the module form only works once the
full dependency set is installed, which defeats the point of a key you may need
before a deploy. The script form has no imports beyond the stdlib.
"""
import os
import secrets
import stat
import sys
from pathlib import Path

KEY_DIR = Path(__file__).resolve().parents[2] / "keys"
KEY_PATH = KEY_DIR / "evidence.key"
KEY_BYTES = 32  # 256-bit; hex-encoded to 64 chars


def main() -> int:
    if KEY_PATH.exists():
        print(f"REFUSING to overwrite existing key: {KEY_PATH}")
        print("Rotating the key invalidates every bundle already sealed with it.")
        print("If you must rotate, move the old file aside first and re-verify")
        print("any evidence you intend to rely on.")
        return 1

    KEY_DIR.mkdir(parents=True, exist_ok=True)
    # Create with restrictive perms from the outset -- writing then chmod-ing
    # leaves a window where the key is world-readable.
    fd = os.open(str(KEY_PATH), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(secrets.token_hex(KEY_BYTES))

    mode = stat.S_IMODE(KEY_PATH.stat().st_mode)
    if mode != 0o600:
        os.chmod(KEY_PATH, 0o600)
        print(f"tightened permissions on {KEY_PATH}")

    print(f"key written: {KEY_PATH} ({KEY_BYTES * 2} hex chars, mode 600)")
    print()
    print("Now export it and start the backend in THIS shell:")
    print()
    print(f'  export ROUTE_ONE_EVIDENCE_KEY_FILE="{KEY_PATH}"')
    print("  python -m app.main")
    print()
    print("Back this file up. Lose it and previously sealed bundles")
    print("can no longer be verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
