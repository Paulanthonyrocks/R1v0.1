import logging
from typing import List, Dict, Any
import numpy as np

logger = logging.getLogger(__name__)

# Severity weights for different distress types
SEVERITY_WEIGHTS = {
    'LOW': 1,
    'MEDIUM': 2,
    'HIGH': 3
}

# Impact weights for different distress types
DISTRESS_WEIGHTS = {
    'pothole': 1.0,
    'longitudinal_crack': 0.7,
    'transverse_crack': 0.7,
    'alligator_crack': 0.8,
    'rutting': 0.9
}

def determine_severity(measurements: Dict[str, float], distress_type: str) -> str:
    """
    Determine the severity level of a distress based on its measurements
    """
    if distress_type == 'pothole':
        depth = measurements.get('depth', 0)
        if depth < 25:  # mm
            return 'LOW'
        elif depth < 50:
            return 'MEDIUM'
        else:
            return 'HIGH'
    elif 'crack' in distress_type:
        width = measurements.get('width', 0)
        if width < 3:  # mm
            return 'LOW'
        elif width < 6:
            return 'MEDIUM'
        else:
            return 'HIGH'
    elif distress_type == 'rutting':
        depth = measurements.get('depth', 0)
        if depth < 10:  # mm
            return 'LOW'
        elif depth < 20:
            return 'MEDIUM'
        else:
            return 'HIGH'
    return 'MEDIUM'  # Default case

def calculate_pci(distresses: List[Dict[str, Any]]) -> float:
    """
    Calculate Pavement Condition Index (PCI) based on detected distresses
    
    Args:
        distresses: List of detected distresses with their measurements
        
    Returns:
        PCI score (0-100)
    """
    try:
        if not distresses:
            return 100.0
            
        total_deduct = 0
        max_deduct = 100
        
        for distress in distresses:
            distress_type = distress['type']
            measurements = distress.get('measurements', {})
            
            # Determine severity
            severity = determine_severity(measurements, distress_type)
            
            # Calculate deduct value
            base_deduct = DISTRESS_WEIGHTS.get(distress_type, 0.5) * SEVERITY_WEIGHTS.get(severity, 1)
            
            # Apply area/length factor if available
            area = measurements.get('area', 0)
            length = measurements.get('length', 0)
            size_factor = np.log1p(max(area, length)) if (area or length) else 1
            
            deduct_value = base_deduct * size_factor
            total_deduct += deduct_value
            
        # Calculate final PCI
        total_deduct = min(total_deduct, max_deduct)  # Cap total deduct value
        pci = max(0, 100 - total_deduct)
        
        return round(pci, 1)
        
    except Exception as e:
        logger.error(f"Error calculating PCI: {str(e)}")
        return 0.0
