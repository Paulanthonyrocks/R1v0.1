"""Export yolo model to TensorRT engine on a T4 GPU.

Run once on the 2×T4 inference box:

    python scripts/export_tensorrt.py

This creates backend/models/yolov8n.engine. On the next backend restart
the inference worker auto-loads the engine file (inference_worker.py:205)
and drops inference latency ~4-8x vs float32 PyTorch forward.

Prerequisites (on the T4 box):
    pip install ultralytics>=8.0.0 nvidia-tensorrt nvidia-pyindex
"""

import os
import sys

PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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

    # ── Export for the backend's actual ingest resolution ──
    # FrameReader clips to (320, 240); the worker's _preprocess_frame
    # ROI-crops but preserves pixel space, so the model receives
    # frames at this native size. Exporting at 320 eliminates the
    # implicit letterbox resize inside YOLO's forward.
    print("Exporting to TensorRT engine (imgsz=320, fp16, batch=1)...")
    try:
        model.export(
            format="engine",
            imgsz=320,
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