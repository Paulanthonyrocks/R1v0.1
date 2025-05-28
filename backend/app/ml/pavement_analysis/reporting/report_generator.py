import logging
from pathlib import Path
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
import cv2
import numpy as np
import matplotlib.pyplot as plt
from enum import Enum

logger = logging.getLogger(__name__)

# Define DistressType enum before it's used
class DistressType(Enum):
    POTHOLE = "pothole"
    LONGITUDINAL_CRACK = "longitudinal_crack"
    TRANSVERSE_CRACK = "transverse_crack"
    ALLIGATOR_CRACK = "alligator_crack"
    RUTTING = "rutting"
    UNKNOWN_DISTRESS = "unknown_distress"

# Define consistent color mapping for distress types for visualization
DISTRESS_COLORS = {
    DistressType.POTHOLE.value: (255, 0, 0),  # Blue
    DistressType.LONGITUDINAL_CRACK.value: (0, 0, 255),  # Red
    DistressType.TRANSVERSE_CRACK.value: (0, 128, 255), # Orange
    DistressType.ALLIGATOR_CRACK.value: (0, 255, 255), # Yellow
    DistressType.RUTTING.value: (0, 255, 0),  # Green
    DistressType.UNKNOWN_DISTRESS.value: (128, 128, 128) # Gray
}

def generate_analysis_report(
    image_path: str,
    distresses: List[Dict[str, Any]],
    pci_score: float,
    output_dir: str,
    base_filename: str,
    include_visualizations: bool = True
) -> Dict[str, str]:
    """
    Generate analysis report with detected distresses and PCI score
    
    Args:
        image_path: Path to the analyzed image
        distresses: List of detected distresses with measurements
        pci_score: Calculated PCI score
        output_dir: Directory to save report and visualizations
        base_filename: Base name for output files
        include_visualizations: Whether to include visualization images
        
    Returns:
        Dictionary with paths to generated report files
    """
    try:
        # Create output directory if it doesn't exist
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        
        # Use the provided base_filename for report files
        report_file_name = f"{base_filename}_analysis_report.json"
        vis_file_name = f"{base_filename}_visualization.png"
        plot_file_name = f"{base_filename}_distress_distribution.png"
        
        # Prepare report data
        report_data = {
            "analysis_timestamp": datetime.now().isoformat(),
            "original_image_ref": base_filename,
            "pci_score": round(pci_score, 2),
            "distresses": distresses,
            "summary": {
                "total_distresses": len(distresses),
                "distress_types_count": {}
            }
        }
        
        for distress in distresses:
            # Ensure 'type' exists and is a string, which is expected if it comes from DistressType.value
            d_type = distress.get("type", DistressType.UNKNOWN_DISTRESS.value) 
            if not isinstance(d_type, str):
                 # Fallback if d_type is not a string (e.g. if DistressType enum itself was passed)
                 try: d_type = d_type.value
                 except: d_type = str(d_type)

            current_count = report_data["summary"]["distress_types_count"].get(d_type, 0)
            report_data["summary"]["distress_types_count"][d_type] = current_count + 1
            
        # Save JSON report
        report_file_path = output_path / report_file_name
        with open(report_file_path, 'w') as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)
            
        output_files = {"report": str(report_file_path)}
        logger.info(f"JSON report saved: {report_file_path}")
        
        if include_visualizations:
            # Generate visualization
            image = cv2.imread(image_path)
            if image is not None:
                vis_image = image.copy()
                # Draw distresses
                for distress in distresses:
                    bbox = distress.get('location')
                    d_type_str = distress.get("type", DistressType.UNKNOWN_DISTRESS.value)
                    if not isinstance(d_type_str, str): d_type_str = d_type_str.value # ensure string key

                    color = DISTRESS_COLORS.get(d_type_str, DISTRESS_COLORS[DistressType.UNKNOWN_DISTRESS.value])
                    
                    if bbox and all(k in bbox for k in ('x1', 'y1', 'x2', 'y2')):
                        x1, y1, x2, y2 = int(bbox['x1']), int(bbox['y1']), int(bbox['x2']), int(bbox['y2'])
                        cv2.rectangle(vis_image, (x1, y1), (x2, y2), color, 2)
                        
                        label_parts = [d_type_str]
                        if 'confidence' in distress and distress['confidence'] is not None:
                            label_parts.append(f"{distress['confidence']:.2f}")
                        
                        # Add key measurements from the 'measurements' dict
                        measurements = distress.get('measurements', {})
                        if 'width_mm' in measurements: label_parts.append(f"W:{measurements['width_mm']:.1f}mm")
                        if 'length_mm' in measurements: label_parts.append(f"L:{measurements['length_mm']:.1f}mm")
                        if 'estimated_diameter_mm' in measurements: label_parts.append(f"D:{measurements['estimated_diameter_mm']:.1f}mm")
                        if 'area_sq_m' in measurements and 'crack' not in d_type_str: label_parts.append(f"A:{measurements['area_sq_m']:.2f}m2")

                        label = " ".join(label_parts)
                        (label_width, label_height), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
                        text_bg_y_start = max(0, y1 - label_height - baseline - 2)
                        text_y_pos = max(label_height + baseline // 2, y1 - baseline // 2 - 2)

                        cv2.rectangle(vis_image, (x1, text_bg_y_start), (x1 + label_width, y1), color, -1)
                        cv2.putText(vis_image, label, (x1, text_y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,0), 1)
                    else:
                        logger.warning(f"Skipping drawing for distress due to missing/invalid bbox: {distress.get('type')}")
                
                # Add PCI score
                pci_text = f"PCI: {pci_score:.1f}"
                (text_width, text_height), baseline = cv2.getTextSize(pci_text, cv2.FONT_HERSHEY_SIMPLEX, 1, 2)
                cv2.rectangle(vis_image, (5, 5), (15 + text_width, 15 + text_height + baseline), (0,0,0), -1)
                cv2.putText(vis_image, pci_text, (10, 10 + text_height), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
                
                # Save visualization
                vis_file_path = output_path / vis_file_name
                cv2.imwrite(str(vis_file_path), vis_image)
                output_files["visualization"] = str(vis_file_path)
                logger.info(f"Visualization image saved: {vis_file_path}")
                
                # Generate distress distribution plot
                plt.figure(figsize=(10, 6))
                distress_types_counts = report_data["summary"]["distress_types_count"]
                if distress_types_counts:
                    plt.bar(distress_types_counts.keys(), distress_types_counts.values(), color=[DISTRESS_COLORS.get(k, (0.5,0.5,0.5)) for k in distress_types_counts.keys()])
                    plt.title("Distress Distribution")
                    plt.xlabel("Distress Type")
                    plt.ylabel("Count")
                    plt.xticks(rotation=45, ha="right")
                    plt.tight_layout()
                    
                    plot_file_path = output_path / plot_file_name
                    plt.savefig(plot_file_path)
                    plt.close()
                    output_files["plot"] = str(plot_file_path)
                    logger.info(f"Distress distribution plot saved: {plot_file_path}")
                else:
                    logger.info("Skipping distress distribution plot as no distresses were found or counted.")
            else:
                logger.warning(f"Could not read image at {image_path} for visualization.")
        
        return output_files
        
    except Exception as e:
        logger.error(f"Error generating analysis report for {base_filename}: {e}", exc_info=True)
        # Return empty dict or re-raise depending on desired error handling
        # raise # Or return dict with error message
        return {"error": str(e)}
