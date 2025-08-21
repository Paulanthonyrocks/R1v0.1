
import os
from ultralytics import YOLO
from onnxruntime.quantization import quantize_dynamic, QuantType

def main():
    """
    Exports the YOLOv8 model to ONNX and creates a quantized version.
    """
    # Define paths
    model_dir = os.path.join(os.path.dirname(__file__), "models")
    pytorch_model_path = os.path.join(model_dir, "yolov8n.pt")
    onnx_model_path = os.path.join(model_dir, "yolov8n.onnx")
    quantized_model_path = os.path.join(model_dir, "yolov8n_quant.onnx")

    # --- Step 1: Export to ONNX ---
    print(f"Loading PyTorch model from: {pytorch_model_path}")
    if not os.path.exists(pytorch_model_path):
        print(f"ERROR: PyTorch model not found at {pytorch_model_path}")
        return

    try:
        model = YOLO(pytorch_model_path)
        print("Exporting to ONNX format...")
        model.export(format="onnx", imgsz=320, opset=12) # Using the original imgsz for export
        print(f"Successfully exported model to: {onnx_model_path}")
    except Exception as e:
        print(f"ERROR during ONNX export: {e}")
        return

    # --- Step 2: Quantize the ONNX model ---
    print(f"Loading ONNX model from: {onnx_model_path}")
    if not os.path.exists(onnx_model_path):
        print(f"ERROR: ONNX model not found at {onnx_model_path}")
        return

    try:
        print("Performing dynamic quantization (INT8)...")
        quantize_dynamic(
            model_input=onnx_model_path,
            model_output=quantized_model_path,
            weight_type=QuantType.QUInt8,
        )
        print(f"Successfully quantized model and saved to: {quantized_model_path}")
    except Exception as e:
        print(f"ERROR during model quantization: {e}")
        return

    print("\nConversion and quantization complete.")
    print(f"Original ONNX model: {onnx_model_path}")
    print(f"Quantized ONNX model: {quantized_model_path}")

if __name__ == "__main__":
    main()
