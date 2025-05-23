import logging
from pathlib import Path
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
import cv2
import numpy as np
import matplotlib.pyplot as plt

logger = logging.getLogger(__name__)

def generate_analysis_report(
    image_path: str,
    distresses: List[Dict[str, Any]],
    pci_score: float,
    output_dir: str,
    include_visualizations: bool = True
) -> Dict[str, str]:
    """
    Generate analysis report with detected distresses and PCI score
    
    Args:
        image_path: Path to the analyzed image
        distresses: List of detected distresses with measurements
        pci_score: Calculated PCI score
        output_dir: Directory to save report and visualizations
        include_visualizations: Whether to include visualization images
        
    Returns:
        Dictionary with paths to generated report files
    """
    try:
        # Create output directory if it doesn't exist
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        # Generate report timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        # Prepare report data
        report_data = {
            "timestamp": timestamp,
            "image_path": image_path,
            "pci_score": pci_score,
            "distresses": distresses,
            "summary": {
                "total_distresses": len(distresses),
                "distress_types": {}
            }
        }
        
        # Count distress types
        for distress in distresses:
            d_type = distress["type"]
            if d_type not in report_data["summary"]["distress_types"]:
                report_data["summary"]["distress_types"][d_type] = 0
            report_data["summary"]["distress_types"][d_type] += 1
            
        # Save JSON report
        report_file = output_path / f"pavement_analysis_{timestamp}.json"
        with open(report_file, 'w') as f:
            json.dump(report_data, f, indent=2)
            
        output_files = {"report": str(report_file)}
        
        if include_visualizations:
            # Generate visualization
            image = cv2.imread(image_path)
            if image is not None:
                # Draw distresses
                for distress in distresses:
                    bbox = distress.get('bbox', {})
                    x1, y1 = int(bbox.get('x1', 0)), int(bbox.get('y1', 0))
                    x2, y2 = int(bbox.get('x2', 0)), int(bbox.get('y2', 0))
                    
                    # Draw rectangle
                    cv2.rectangle(image, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    
                    # Add label
                    label = f"{distress['type']} ({distress.get('confidence', 0):.2f})"
                    cv2.putText(image, label, (x1, y1-10), 
                              cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
                
                # Add PCI score
                cv2.putText(image, f"PCI: {pci_score:.1f}", (10, 30),
                          cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                
                # Save visualization
                vis_file = output_path / f"visualization_{timestamp}.png"
                cv2.imwrite(str(vis_file), image)
                output_files["visualization"] = str(vis_file)
                
                # Generate distress distribution plot
                plt.figure(figsize=(10, 6))
                distress_types = report_data["summary"]["distress_types"]
                plt.bar(distress_types.keys(), distress_types.values())
                plt.title("Distress Distribution")
                plt.xticks(rotation=45)
                plt.tight_layout()
                
                # Save plot
                plot_file = output_path / f"distress_distribution_{timestamp}.png"
                plt.savefig(plot_file)
                plt.close()
                output_files["plot"] = str(plot_file)
        
        return output_files
        
    except Exception as e:
        logger.error(f"Error generating analysis report: {str(e)}")
        raise
