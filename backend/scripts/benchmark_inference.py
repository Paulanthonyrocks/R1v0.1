import time
import argparse
import torch
import cv2
import numpy as np
from pathlib import Path
from ultralytics import YOLO

def benchmark_yolo(model_path, imgsz=640, num_frames=100, device='cuda'):
    print(f"--- Benchmarking model: {model_path} ---")
    print(f"Device: {device}, Image Size: {imgsz}, Frames: {num_frames}")
    
    try:
        model = YOLO(model_path)
        model.to(device)
    except Exception as e:
        print(f"Error loading model: {e}")
        return

    # Warmup
    print("Warming up...")
    dummy_frame = np.random.randint(0, 255, (imgsz, imgsz, 3), dtype=np.uint8)
    for _ in range(10):
        model.predict(dummy_frame, imgsz=imgsz, verbose=False)

    # Benchmark
    print("Running benchmark...")
    start_time = time.time()
    for _ in range(num_frames):
        model.predict(dummy_frame, imgsz=imgsz, verbose=False)
    end_time = time.time()

    total_time = end_time - start_time
    avg_fps = num_frames / total_time
    avg_ms = (total_time / num_frames) * 1000

    print(f"\nResults:")
    print(f"Total Time: {total_time:.2f}s")
    print(f"Average FPS: {avg_fps:.2f}")
    print(f"Average Inference Time: {avg_ms:.2f}ms")
    print("-" * 40)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Benchmark YOLO model performance.")
    parser.add_argument("--model", type=str, required=True, help="Path to model file (.pt, .engine, .onnx)")
    parser.add_argument("--imgsz", type=int, default=640, help="Inference image size")
    parser.add_argument("--frames", type=int, default=100, help="Number of frames to benchmark")
    parser.add_argument("--device", type=str, default="cuda" if torch.cuda.is_available() else "cpu", help="Device to run on")
    
    args = parser.parse_args()
    
    benchmark_yolo(args.model, args.imgsz, args.frames, args.device)
