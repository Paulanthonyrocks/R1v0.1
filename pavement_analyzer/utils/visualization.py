# utils/visualization.py
import cv2
import numpy as np
import logging

logger = logging.getLogger(__name__)

DISTRESS_COLORS = {
    'longitudinal_crack': (0, 0, 255),   # Red
    'transverse_crack':   (0, 128, 255), # Orange
    'alligator_crack':    (0, 255, 255), # Yellow
    'pothole':            (255, 0, 0),   # Blue
    'rutting':            (0, 255, 0),   # Green
    'unknown_crack':      (200, 0, 200), # Magenta for generic cracks
    'unknown_distress':   (128, 128, 128), # Gray
    'unknown_class':      (100, 100, 100)
}

def draw_dl_detections(image, dl_detections): # Renamed for clarity if it handles all detections
    """
    Draws bounding boxes and labels from a list of detection dictionaries.
    Each detection dict: {'box': [x,y,w,h], 'class_name': str, 'score': float, 'measurements': dict}
    """
    vis_image = image.copy()
    if not dl_detections:
        return vis_image

    for det in dl_detections:
        box = det.get('box')
        class_name = det.get('class_name', 'unknown_distress')
        score = det.get('score', 0.0) # Default score to 0.0 if not present
        measurements = det.get('measurements', {})

        if box is None or len(box) != 4:
            logger.warning(f"Skipping detection with invalid box: {det}")
            continue
            
        color = DISTRESS_COLORS.get(class_name, DISTRESS_COLORS['unknown_distress'])
        x, y, w, h = map(int, box)
        cv2.rectangle(vis_image, (x, y), (x + w, y + h), color, 2)

        label_parts = [f"{class_name}"]
        if score > 0: # Only show score if it's meaningful
             label_parts.append(f"{score:.2f}")
        
        # Add key measurements to label
        if 'width_mm' in measurements:
            label_parts.append(f"W:{measurements['width_mm']:.1f}mm")
        if 'length_mm' in measurements:
            label_parts.append(f"L:{measurements['length_mm']:.1f}mm")
        if 'area_sq_m' in measurements and 'crack' not in class_name : # Area for non-cracks
            label_parts.append(f"A:{measurements['area_sq_m']:.2f}m2")

        label = " ".join(label_parts)
        
        (label_width, label_height), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        # Ensure text background does not go off-screen (top)
        text_bg_y_start = max(0, y - label_height - baseline)
        text_y_pos = max(label_height + baseline // 2, y - baseline // 2) # Ensure text y is within image

        cv2.rectangle(vis_image, (x, text_bg_y_start), (x + label_width, y), color, -1)
        cv2.putText(vis_image, label, (x, text_y_pos), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,0,0), 1) # Black text
    return vis_image

def display_pci_on_image(image, pci_score):
    vis_image = image.copy()
    text = f"PCI: {pci_score:.2f}"
    font_scale = 1.0
    thickness = 2
    (text_width, text_height), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness)
    
    margin = 15
    text_x = margin
    text_y = text_height + margin

    cv2.rectangle(vis_image, (text_x - 5, text_y - text_height - 5 - baseline),
                  (text_x + text_width + 5, text_y + 5), (0,0,0, 150), -1) # Black background with alpha
    cv2.putText(vis_image, text, (text_x, text_y - baseline//2), cv2.FONT_HERSHEY_SIMPLEX, font_scale, (255, 255, 255), thickness, cv2.LINE_AA)
    return vis_image

def add_legend(image, active_distress_types=None):
    vis_image = image.copy()
    legend_item_height = 25
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 0.5
    font_thickness = 1
    text_color = (255, 255, 255)
    
    # Use only colors that are relevant or a predefined subset for legend
    display_items = []
    relevant_colors = {
        'longitudinal_crack': DISTRESS_COLORS['longitudinal_crack'],
        'pothole': DISTRESS_COLORS['pothole'],
        'alligator_crack': DISTRESS_COLORS['alligator_crack'],
        'rutting': DISTRESS_COLORS.get('rutting', (0,255,0)) # Ensure rutting is defined
    }
    # If active_distress_types is provided, filter (more complex logic needed here)
    
    for name, color_bgr in relevant_colors.items():
        display_items.append((name.replace("_", " ").title(), color_bgr))
    
    if not display_items: return vis_image

    max_text_width = max(cv2.getTextSize(name, font, font_scale, font_thickness)[0][0] for name, _ in display_items)
    legend_width = max_text_width + 50 # Color box + padding
    legend_height = len(display_items) * legend_item_height + 10

    legend_start_x = vis_image.shape[1] - legend_width - 10 # Top-right
    legend_start_y = 10

    sub_img = vis_image[legend_start_y : legend_start_y + legend_height, legend_start_x : legend_start_x + legend_width]
    black_rect = np.full(sub_img.shape, (0, 0, 0), dtype=np.uint8) # Opaque black background
    res = cv2.addWeighted(sub_img, 0.5, black_rect, 0.5, 1.0) # Blend for semi-transparency
    vis_image[legend_start_y : legend_start_y + legend_height, legend_start_x : legend_start_x + legend_width] = res

    current_y = legend_start_y + legend_item_height // 2 + 5
    for name, color_bgr in display_items:
        cv2.rectangle(vis_image, (legend_start_x + 5, current_y - legend_item_height // 2 + 5),
                      (legend_start_x + 25, current_y + legend_item_height // 2 - 5),
                      color_bgr, -1)
        cv2.putText(vis_image, name, (legend_start_x + 35, current_y + 5),
                    font, font_scale, text_color, font_thickness, cv2.LINE_AA)
        current_y += legend_item_height
        
    return vis_image