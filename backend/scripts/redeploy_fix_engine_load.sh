#!/usr/bin/env bash
# Diagnose + force-redeploy the inference-worker TensorRT engine-load fix on the
# kaggle (2xT4) box. Run THIS on the box:  bash redeploy_fix_engine_load.sh
#
# Symptom it fixes: every Worker N logs
#   "TensorRT engine loaded successfully."  then
#   "Shared model load exception: ... should be a *.pt PyTorch model ..."  then
#   "Shared model load failed. Exiting."  (watchdog respawn loop, never ready)
#
# Root cause: the deployed copy of backend/app/core/inference_worker.py is a
# PRE-FIX checkout where the engine YOLO call is BARE (YOLO(str(engine_path))),
# not the fixed YOLO(str(engine_path), task="detect"). This script proves that,
# forces the box onto the fixed commit, clears stale .pyc, and restarts.

set -u
REPO="${1:-/kaggle/working/R1v0.1}"
cd "$REPO" || { echo "Cannot cd to $REPO"; exit 1; }

echo "=== [1/5] Verify the deployed file ACTUALLY has the fix ==="
if grep -q 'YOLO(str(engine_path), task="detect")' backend/app/core/inference_worker.py; then
  echo "  OK: deployed file contains the task=detect fix."
else
  echo "  PROOF OF DIVERGENCE: deployed inference_worker.py is MISSING task=detect."
  echo "  This is the pre-fix code. The repo fix never reached this box."
fi

echo
echo "=== [2/5] Verify the engine loads under task=detect on THIS box's ultralytics ==="
uv run --no-project python - <<'PY' 2>&1 | grep -v Warning
from pathlib import Path
import glob, os
eng = glob.glob("backend/models/yolov8n.engine") or glob.glob("models/yolov8n.engine")
if not eng:
    print("  engine not found next to backend/ -- adjust path. (expected at backend/models/yolov8n.engine)")
else:
    from ultralytics import YOLO
    m = YOLO(eng[0], task="detect")
    print("  ENGINE LOADS OK under task='detect':", eng[0])
PY

echo
echo "=== [3/5] Force the box onto the fixed commit (ignores local test/.gemini changes) ==="
git fetch origin -q
# stash any local tracked modifications so the checkout is clean
git stash -u -q 2>/dev/null && echo "  (stashed local changes)"
git checkout -f 593a9a7 2>&1 | tail -1
git log -1 --format="  now at %h %ci %s"

echo
echo "=== [4/5] Clear stale bytecode so no pre-fix .pyc is loaded ==="
find backend/app -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null
echo "  cleared __pycache__ under backend/app"

echo
echo "=== [5/5] Re-confirm the fix is present after checkout ==="
if grep -q 'YOLO(str(engine_path), task="detect")' backend/app/core/inference_worker.py; then
  echo "  OK: fix now in place. Restart the backend now."
else
  echo "  STILL MISSING -- the box is not on branch/main you expect. Inspect: git log -1"
fi

echo
echo "Next: stop the running backend, then start it (your launch cmd), and watch:"
echo "  tail -f backend/logs/backend_main.log"
echo "Every Worker N should now log 'TensorRT engine loaded successfully.' and reach"
echo "readiness (no more 'Shared model load failed. Exiting.')."
