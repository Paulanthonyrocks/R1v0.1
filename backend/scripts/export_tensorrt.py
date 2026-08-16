"""Export yolo model to TensorRT engine on a T4 GPU.

Run once on the 2×T4 inference box:

    python scripts/export_tensorrt.py

This creates backend/models/yolov8n.engine. On the next backend restart
the inference worker auto-loads the engine file (inference_worker.py:205)
and drops inference latency ~4-8x vs float32 PyTorch forward.

The engine imgsz is read from config.yaml vehicle_detection.yolo_imgsz so
the export always matches what the worker requests at runtime. CONTRACT:
re-run this script after changing yolo_imgsz -- a stale engine (exported at
a different imgsz) makes the worker's static-batch call fail at boot.

Prerequisites (on the T4 box):
    pip install ultralytics>=8.0.0 nvidia-tensorrt nvidia-pyindex
"""

import os
import sys

PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_imgsz_from_config() -> int:
    """yolo_imgsz from config.yaml (single source of truth with the worker)."""
    try:
        import yaml

        with open(os.path.join(PROJ_ROOT, "configs", "config.yaml")) as f:
            cfg = yaml.safe_load(f)
        return int(cfg["vehicle_detection"].get("yolo_imgsz", 640))
    except Exception as e:
        print(f"WARNING: could not read yolo_imgsz from config ({e}); using 320.", file=sys.stderr)
        return 320


def main():
    model_pt = os.path.join(PROJ_ROOT, "models", "yolov8n.pt")
    engine_out = os.path.join(PROJ_ROOT, "models", "yolov8n.engine")

    if not os.path.exists(model_pt):
        print(
            f"ERROR: source model not found at {model_pt}.\n"
            "Download yolov8n.pt first (ultralytics auto-pulls it on first YOLO('yolov8n.pt') load).",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        from ultralytics import YOLO
    except ImportError:
        print(
            "ERROR: ultralytics not installed.\n"
            "Run: pip install ultralytics>=8.0.0 nvidia-tensorrt nvidia-pyindex",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Loading PyTorch model from {model_pt} ...")
    model = YOLO(model_pt)

    imgsz = _read_imgsz_from_config()
    print(f"Exporting to TensorRT engine (imgsz={imgsz}, fp16, batch=1)...")
    try:
        model.export(
            format="engine",
            imgsz=imgsz,
            half=True,          # fp16 -> ~2x speed on T4 vs fp32
            batch=1,            # backend batches independently via YOLO( list )
            workspace=4,        # GB; T4 has 16, but 4 is plenty
        )
    except Exception as e:
        print(f"ERROR: TensorRT export failed: {e}", file=sys.stderr)
        sys.exit(1)

    # export writes to models/yolov8n.engine by default; verify
    if os.path.exists(engine_out):
        print(f"\nTensorRT engine written to {engine_out}")
        sz_mb = os.path.getsize(engine_out) / (1024 * 1024)
        print(f"Size: {sz_mb:.1f} MB")
        print(
            "Ready. Restart the backend; the inference worker will "
            "auto-load this engine on next boot (inference_worker.py line 207)."
        )
    else:
        print("WARNING: engine file not found at expected path; check ultralytics output.",
              file=sys.stderr)


if __name__ == "__main__":
    main()