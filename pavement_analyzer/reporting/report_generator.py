# reporting/report_generator.py
import logging
import json
# import pandas as pd # Optional: if generating more complex reports
# import matplotlib.pyplot as plt # Optional: for plotting

logger = logging.getLogger(__name__)

def generate_analysis_report(image_filename, distress_data, pci_score, output_dir='reports/'):
    """
    Generates a basic analysis report.
    
    Args:
        image_filename: The name of the image file analyzed.
        distress_data: List of detected and measured distresses.
        pci_score: The calculated PCI score.
        output_dir: Directory to save the report.
        
    Returns:
        The path to the generated report file.
    """
    report_filename = f"{output_dir}{image_filename}_analysis_report.json"
    
    report_data = {
        'image_filename': image_filename,
        'pci_score': round(pci_score, 2),
        'distress_counts': {},
        'distress_summary': [], # Basic summary of each distress
        'raw_distress_data': distress_data # Include raw data for details
    }

    # Summarize distress counts and basic info
    distress_counts = {}
    for distress in distress_data:
        class_name = distress.get('class_name', 'unknown')
        distress_counts[class_name] = distress_counts.get(class_name, 0) + 1
        
        summary_item = {
            'class_name': class_name,
            'measurements': distress.get('measurements', {})
        }
        report_data['distress_summary'].append(summary_item)
        
    report_data['distress_counts'] = distress_counts

    # Ensure output directory exists
    import os
    os.makedirs(output_dir, exist_ok=True)

    try:
        with open(report_filename, 'w') as f:
            json.dump(report_data, f, indent=4)
        logger.info(f"Analysis report generated: {report_filename}")
        return report_filename
    except Exception as e:
        logger.error(f"Error generating report {report_filename}: {e}")
        return None

# TODO: Implement more sophisticated reporting:
# - HTML or PDF reports
# - Include visual summaries (e.g., image with detections)
# - Detailed tables of each distress
# - Section-by-section PCI if analyzing a road segment
# - Integration with external reporting libraries (e.g., ReportLab, fpdf2)