# analysis_modules/pci_calculator.py
import logging
# from .distress_definitions import PCI_DEDUCT_VALUES # Future: load PCI curves/tables

logger = logging.getLogger(__name__)

# Placeholder PCI calculation logic
# This is a highly simplified example. Real PCI calculation involves detailed
# distress types, severity levels, quantities, and deduct value curves.

def calculate_pci(distress_data):
    """
    Calculates a simplified Pavement Condition Index (PCI) based on detected distresses.
    
    Args:
        distress_data: A list of dictionaries, each representing a detected and measured distress.
                       Expected keys: 'class_name', 'measurements' (which contains size info like area_sq_m)
                       Example: [{'class_name': 'pothole', 'measurements': {'area_sq_m': 0.5}}, ...]
                       
    Returns:
        A simplified PCI score (0-100).
    """
    if not distress_data:
        logger.info("No distress data provided. Returning perfect PCI (100).")
        return 100.0

    # Simplified deduction logic:
    # Assign a base deduct value per distress type and scale by its size/severity.
    # This is NOT how real PCI works, but serves as a conceptual placeholder.
    
    total_deduct = 0
    
    # Example: Base deduct values (very arbitrary)
    base_deduct_per_sq_m = {
        'pothole': 50,       # High impact per area
        'alligator_crack': 30, # Medium-high impact per area
        'rutting': 20,       # Medium impact per area
        # Cracks are typically based on length and width, not just area in PCI
        'longitudinal_crack': 10, # Lower impact per area (simplified)
        'transverse_crack': 10,
        'unknown_crack': 15,
        'unknown_distress': 20
    }
    
    for distress in distress_data:
        class_name = distress.get('class_name', 'unknown_distress')
        measurements = distress.get('measurements', {})
        area_sq_m = measurements.get('area_sq_m', 0) # Assume area is available

        if area_sq_m > 0:
            base_deduct = base_deduct_per_sq_m.get(class_name, base_deduct_per_sq_m['unknown_distress'])
            deduct = base_deduct * area_sq_m # Simple linear scaling (not realistic PCI)
            total_deduct += deduct
            logger.debug(f"Distress {class_name} (Area: {area_sq_m:.2f} m2) added deduct: {deduct:.2f}")
        elif class_name in ['longitudinal_crack', 'transverse_crack'] and 'length_mm' in measurements and 'width_mm' in measurements:
             # Very simplified crack length/width based deduct
             length_m = measurements['length_mm'] / 1000.0
             width_mm = measurements['width_mm']
             # PCI uses severity levels (low, medium, high) based on width and length criteria
             # Simplified: deduct more for wider cracks or longer cracks
             deduct = (length_m * 2 + width_mm * 0.5) # Arbitrary formula
             total_deduct += deduct
             logger.debug(f"Crack {class_name} (L:{length_m:.2f}m, W:{width_mm:.1f}mm) added deduct: {deduct:.2f}")


    # Cap total deduct at a high value to prevent negative PCI (though PCI is 0-100)
    max_possible_deduct = 100 # Or higher if needed for intermediate calculation
    total_deduct = min(total_deduct, max_possible_deduct)

    pci_score = 100 - total_deduct

    # Final PCI is between 0 and 100
    pci_score = max(0, pci_score)
    pci_score = min(100, pci_score)


    logger.info(f"Calculated simplified PCI: {pci_score:.2f}")
    return pci_score

# TODO: Implement proper PCI calculation using standard ASTM D6433 or similar.
# This requires defining severity levels for each distress type and implementing
# the iterative PCI calculation process with corrected deduct values.
# Need to load or define PCI deduct curves/tables based on quantity and severity.