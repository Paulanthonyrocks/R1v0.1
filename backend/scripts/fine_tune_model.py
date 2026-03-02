import os
import shutil
import logging
import json
from pathlib import Path
from ultralytics import YOLO
import torch

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("model_finetune")

def fine_tune(
    base_model_path: str,
    new_data_path: str,
    baseline_data_path: str,
    output_dir: str,
    epochs: int = 10,
    batch_size: int = 16
):
    """
    Fine-tunes a model on new hard negatives while mixing in baseline data
    to prevent catastrophic forgetting.
    """
    project_root = Path(output_dir)
    project_root.mkdir(parents=True, exist_ok=True)
    
    # 1. Prepare Mixed Dataset
    # We create a temporary merged dataset folder
    merged_path = project_root / "merged_dataset"
    img_dir = merged_path / "images"
    lbl_dir = merged_path / "labels"
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)
    
    def copy_data(src_root, ratio=1.0):
        src_imgs = Path(src_root) / "images"
        src_lbls = Path(src_root) / "labels"
        files = list(src_imgs.glob("*.jpg"))
        
        # Simple sampling logic if we want to limit baseline data
        count = int(len(files) * ratio)
        for f in files[:count]:
            shutil.copy(str(f), str(img_dir / f.name))
            l_name = f.stem + ".txt"
            if (src_lbls / l_name).exists():
                shutil.copy(str(src_lbls / l_name), str(lbl_dir / l_name))

    logger.info("Merging new hard negatives with baseline data...")
    copy_data(new_data_path, ratio=1.0) # All new data
    if baseline_data_path and Path(baseline_data_path).exists():
        # Mix 1:4 ratio (25% new, 75% old) is often recommended for stability
        # But here we'll just take what's available
        copy_data(baseline_data_path, ratio=1.0)
    
    # Create dataset.yaml
    with open(merged_path / "dataset.yaml", "w") as f:
        # Note: Paths in dataset.yaml should be absolute or relative to the yaml
        f.write(f"path: {merged_path.absolute()}
")
        f.write("train: images
val: images

")
        # Copy names from base model
        model = YOLO(base_model_path)
        f.write("names:
")
        for k, v in model.names.items():
            f.write(f"  {k}: {v}
")

    # 2. Run Training
    logger.info(f"Starting fine-tuning for {epochs} epochs...")
    results = model.train(
        data=str(merged_path / "dataset.yaml"),
        epochs=epochs,
        imgsz=640,
        batch=batch_size,
        project=str(project_root),
        name="finetune_run",
        exist_ok=True,
        device='cuda' if torch.cuda.is_available() else 'cpu'
    )
    
    # 3. Export & Cleanup
    best_model = project_root / "finetune_run" / "weights" / "best.pt"
    final_output = project_root / "refined_model.pt"
    if best_model.exists():
        shutil.copy(str(best_model), str(final_output))
        logger.info(f"Fine-tuning complete. Refined model saved to {final_output}")
    
    # Generate simple training report
    report = {
        "base_model": base_model_path,
        "epochs": epochs,
        "new_samples_count": len(list(Path(new_data_path).glob("images/*.jpg"))),
        "total_merged_samples": len(list(img_dir.glob("*.jpg"))),
        "status": "SUCCESS" if best_model.exists() else "FAILED"
    }
    with open(project_root / "finetune_report.json", "w") as f:
        json.dump(report, f, indent=4)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, help="Base model .pt")
    parser.add_argument("--new", required=True, help="Path to annotation_project folder")
    parser.add_argument("--baseline", help="Path to baseline dataset folder (optional)")
    parser.add_argument("--out", default="backend/data/finetune_output", help="Output directory")
    args = parser.parse_args()
    
    fine_tune(args.base, args.new, args.baseline, args.out)
