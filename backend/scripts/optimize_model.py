import argparse
import os
import sys
from pathlib import Path
from ultralytics import YOLO
import torch

def optimize_model(model_path: str, format: str = "engine", half: bool = True):
    """
    Export a YOLO model to an optimized format (TensorRT engine by default).
    """
    print(f"Loading model from {model_path}...")
    model = YOLO(model_path)
    
    device = "0" if torch.cuda.is_available() else "cpu"
    if device == "cpu" and format == "engine":
        print("WARNING: TensorRT export requires a GPU. Falling back to ONNX.")
        format = "onnx"

    print(f"Exporting model to {format} (half={half}) on device {device}...")
    try:
        # Export the model
        # imgsz should match what's used in core_module.py (default 640)
        exported_path = model.export(format=format, device=device, half=half, imgsz=640)
        print(f"Successfully exported to: {exported_path}")
    except Exception as e:
        print(f"Export failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Optimize YOLO models for Traffic Management Hub")
    parser.add_argument("--model", type=str, required=True, help="Path to the .pt model file")
    parser.add_argument("--format", type=str, default="engine", choices=["engine", "onnx"], help="Export format")
    parser.add_argument("--no-half", action="store_true", help="Disable FP16 quantization")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.model):
        print(f"Error: Model file {args.model} not found.")
        sys.exit(1)
        
    optimize_model(args.model, args.format, not args.no_half)
