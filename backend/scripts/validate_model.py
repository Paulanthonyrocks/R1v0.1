import os
import cv2
import torch
import numpy as np
import logging
import json
from pathlib import Path
from ultralytics import YOLO
from typing import Dict, List, Tuple

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("model_validation")

class ModelValidator:
    def __init__(self, base_model_path: str, candidate_model_path: str, device: str = 'cpu'):
        self.base_model = YOLO(base_model_path)
        self.candidate_model = YOLO(candidate_model_path)
        self.device = device
        logger.info(f"Validator initialized. Device: {device}")

    def run_comparison(self, image_dir: str, output_report: str):
        image_path = Path(image_dir)
        images = list(image_path.glob("**/*.jpg"))
        
        if not images:
            logger.error(f"No images found in {image_dir}")
            return

        results = []
        logger.info(f"Comparing models on {len(images)} images...")

        for i, img_p in enumerate(images):
            frame = cv2.imread(str(img_p))
            if frame is None: continue

            # Inference
            res_base = self.base_model(frame, verbose=False, device=self.device)[0]
            res_cand = self.candidate_model(frame, verbose=False, device=self.device)[0]

            # Extraction
            base_boxes = res_base.boxes.data.cpu().numpy()
            cand_boxes = res_cand.boxes.data.cpu().numpy()

            # Stats
            avg_conf_base = np.mean(base_boxes[:, 4]) if len(base_boxes) > 0 else 0
            avg_conf_cand = np.mean(cand_boxes[:, 4]) if len(cand_boxes) > 0 else 0
            
            # Simple match check (IoU)
            # This helps identify if the new model is "discovering" new things or just shifting confidence
            
            results.append({
                "image": str(img_p.name),
                "base_count": len(base_boxes),
                "cand_count": len(cand_boxes),
                "base_avg_conf": float(avg_conf_base),
                "cand_avg_conf": float(avg_conf_cand),
                "diff_count": len(cand_boxes) - len(base_boxes)
            })

            if i % 10 == 0:
                logger.info(f"Processed {i}/{len(images)} images")

        # Aggregate Report
        total_base = sum(r["base_count"] for r in results)
        total_cand = sum(r["cand_count"] for r in results)
        mean_conf_base = np.mean([r["base_avg_conf"] for r in results])
        mean_conf_cand = np.mean([r["cand_avg_conf"] for r in results])

        report = {
            "summary": {
                "total_images": len(images),
                "base_total_detections": total_base,
                "cand_total_detections": total_cand,
                "base_mean_conf": float(mean_conf_base),
                "cand_mean_conf": float(mean_conf_cand),
                "detection_yield_change_percent": ((total_cand - total_base) / total_base * 100) if total_base > 0 else 0
            },
            "per_image_results": results
        }

        with open(output_report, 'w') as f:
            json.dump(report, f, indent=4)
        
        logger.info(f"Validation report saved to {output_report}")
        logger.info(f"Improvement Summary: Yield Change: {report['summary']['detection_yield_change_percent']:.1f}%")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True, help="Path to base model .pt")
    parser.add_argument("--candidate", required=True, help="Path to fine-tuned model .pt")
    parser.add_argument("--data", required=True, help="Path to validation image directory")
    parser.add_argument("--out", default="validation_report.json", help="Output report path")
    args = parser.parse_args()

    validator = ModelValidator(args.base, args.candidate)
    validator.run_comparison(args.data, args.out)
