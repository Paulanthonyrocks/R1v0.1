import os
import shutil
import json
import logging
from pathlib import Path
from ultralytics import YOLO

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("prepare_annotation")

def prepare_dataset(source_dir: str, output_dir: str, model_path: str):
    source_path = Path(source_dir)
    dest_path = Path(output_dir)
    
    # Create YOLO structure
    img_dir = dest_path / "images"
    lbl_dir = dest_path / "labels"
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)
    
    # Load model for pre-labeling
    model = YOLO(model_path)
    
    # Find all processed hard negatives
    images = list(source_path.glob("**/*.jpg"))
    logger.info(f"Found {len(images)} images to prepare.")
    
    for img_p in images:
        # 1. Copy image
        target_img = img_dir / img_p.name
        shutil.copy(str(img_p), str(target_img))
        
        # 2. Generate pre-labels
        res = model(str(img_p), verbose=False)[0]
        h, w = res.orig_shape
        
        label_file = lbl_dir / f"{img_p.stem}.txt"
        with open(label_file, "w") as lf:
            for box in res.boxes.data.cpu().numpy():
                x1, y1, x2, y2, conf, cls = box
                # YOLO format: cls x_center y_center width height (normalized)
                xc = (x1 + x2) / 2 / w
                yc = (y1 + y2) / 2 / h
                bw = (x2 - x1) / w
                bh = (y2 - y1) / h
                lf.write(f"{int(cls)} {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}
")
                
    # 3. Create dataset.yaml
    with open(dest_path / "dataset.yaml", "w") as f:
        f.write(f"path: {dest_path.absolute()}
")
        f.write("train: images
val: images

")
        f.write("names:
")
        for k, v in model.names.items():
            f.write(f"  {k}: {v}
")
            
    logger.info(f"Dataset prepared at {output_dir}. Ready for import into Label Studio.")

if __name__ == "__main__":
    PROJECT_ROOT = Path(__file__).parent.parent.parent
    SRC = PROJECT_ROOT / "backend/data/hard_negative_dataset"
    DEST = PROJECT_ROOT / "backend/data/annotation_project"
    MODEL = PROJECT_ROOT / "backend/models/yolov8n.pt"
    
    prepare_dataset(str(SRC), str(DEST), str(MODEL))
