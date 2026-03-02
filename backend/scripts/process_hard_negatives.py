import os
import shutil
import json
import logging
from pathlib import Path
from datetime import datetime
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("process_hard_negatives")

def process_hard_negatives(source_dir: str, output_dir: str):
    source_path = Path(source_dir)
    output_path = Path(output_dir)
    
    if not source_path.exists():
        logger.error(f"Source directory {source_dir} does not exist.")
        return

    logger.info(f"Processing hard negatives from {source_dir}...")
    
    # Organize by feed
    feed_counts = defaultdict(int)
    samples = []
    
    # Format: hard_neg_{feed_id}_{frame_index}_{timestamp}.jpg
    for f in source_path.glob("*.jpg"):
        parts = f.stem.split("_")
        if len(parts) < 4: continue
        
        feed_id = parts[2]
        timestamp = int(parts[4])
        dt = datetime.fromtimestamp(timestamp)
        batch_id = dt.strftime("%Y-%m-%d")
        
        target_dir = output_path / batch_id / feed_id
        target_dir.mkdir(parents=True, exist_ok=True)
        
        # Move file
        target_file = target_dir / f.name
        shutil.move(str(f), str(target_file))
        
        feed_counts[feed_id] += 1
        samples.append({
            "filename": f.name,
            "feed_id": feed_id,
            "timestamp": timestamp,
            "date": batch_id,
            "path": str(target_file.relative_to(output_path.parent))
        })

    if samples:
        # Generate summary
        summary = {
            "last_processed": datetime.now().isoformat(),
            "total_samples": len(samples),
            "feed_distribution": dict(feed_counts),
            "batches": sorted(list(set(s["date"] for s in samples)))
        }
        
        with open(output_path / "summary.json", "w") as sf:
            json.dump(summary, sf, indent=4)
            
        logger.info(f"Successfully processed {len(samples)} samples into {output_dir}")
        logger.info(f"Feed distribution: {dict(feed_counts)}")
    else:
        logger.info("No new samples to process.")

if __name__ == "__main__":
    # Default paths
    PROJECT_ROOT = Path(__file__).parent.parent.parent
    SRC = PROJECT_ROOT / "backend/data/hard_negatives"
    DEST = PROJECT_ROOT / "backend/data/hard_negative_dataset"
    
    process_hard_negatives(str(SRC), str(DEST))
