import numpy as np
from typing import Dict, Any

# It's good practice to have logging available in all modules
import logging
logger = logging.getLogger(__name__)

class TrafficMonitor:
    # Class attribute: Mapping of vehicle class IDs to their names.
    # This can be expanded or moved to configuration if it becomes more complex.
    vehicle_type_map: Dict[int, str] = {
        2: 'car',
        3: 'motorcycle',
        5: 'bus',
        7: 'truck',
        -1: 'unknown' # Explicitly define unknown for clarity
    }

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.tracked_vehicles: Dict[int, Dict[str, Any]] = {}
        self.lane_counts: Dict[int, int] = {} # Stores count of vehicles per lane_id

        # Extract relevant settings from the main configuration dict
        # Provide defaults if keys are missing to make the class more robust
        self.speed_limit_kmh: float = config.get('speed_limit', 60.0)

        incident_cfg = config.get('incident_detection', {})
        self.density_threshold: int = incident_cfg.get('density_threshold', 10) # Vehicles per lane/area
        self.congestion_speed_threshold: float = incident_cfg.get('congestion_speed_threshold', 20.0) # km/h

        self.stopped_threshold_kmh: float = config.get('stopped_speed_threshold_kmh', 5.0) # km/h

    def update_vehicles(self, vehicles: Dict[int, Dict[str, Any]]):
        """
        Updates the monitor with the latest set of tracked vehicles.
        Args:
            vehicles: A dictionary where keys are track_ids and values are vehicle data dictionaries.
                      Each vehicle data dictionary should ideally contain 'lane', 'speed', 'class_id'.
        """
        self.tracked_vehicles = vehicles
        self.lane_counts.clear() # Reset lane counts for the new update
        for _track_id, data in vehicles.items():
            lane = data.get('lane', -1) # Default to -1 if lane info is missing
            if lane != -1: # Only count if lane info is valid
                self.lane_counts[lane] = self.lane_counts.get(lane, 0) + 1

    def get_metrics(self) -> Dict[str, Any]:
        """
        Calculates and returns various traffic metrics based on the current state of tracked vehicles.
        Returns:
            A dictionary containing metrics like total vehicles, counts of stopped/speeding vehicles,
            average speed, congestion level, vehicle counts per lane, etc.
        """
        total_vehicles = len(self.tracked_vehicles)
        stopped_count = 0
        speeding_count = 0
        speeds_list_kmh: list[float] = []

        # Initialize vehicle type counts, ensuring all defined types in vehicle_type_map are present
        vehicle_type_counts: Dict[str, int] = {name: 0 for name in self.vehicle_type_map.values()}
        # Ensure 'unknown' is also initialized if not already in the map's values (though it is by default now)
        if 'unknown' not in vehicle_type_counts:
             vehicle_type_counts['unknown'] = 0

        for data in self.tracked_vehicles.values():
            speed_kmh = float(data.get('speed', 0.0)) # Default to 0.0 if speed is missing
            speeds_list_kmh.append(speed_kmh)

            if speed_kmh < self.stopped_threshold_kmh:
                stopped_count += 1
            if speed_kmh > self.speed_limit_kmh:
                speeding_count += 1

            class_id = data.get('class_id', -1) # Default to -1 for unknown class_id
            type_name = self.vehicle_type_map.get(class_id, 'unknown') # Get type, default to 'unknown'
            vehicle_type_counts[type_name] = vehicle_type_counts.get(type_name, 0) + 1

        avg_speed_kmh = float(np.mean(speeds_list_kmh)) if speeds_list_kmh else 0.0

        # Congestion level as percentage of stopped vehicles
        congestion_lvl_percent = float((stopped_count / total_vehicles) * 100.0) if total_vehicles > 0 else 0.0

        # Determine if overall congestion is occurring
        is_congested = (avg_speed_kmh < self.congestion_speed_threshold and
                        total_vehicles > self.density_threshold) # Basic congestion heuristic

        # Identify lanes with high density
        high_density_lanes = [lane for lane, count in self.lane_counts.items() if count > self.density_threshold]

        return {
            'total_vehicles': total_vehicles,
            'stopped_vehicles': stopped_count,
            'speeding_vehicles': speeding_count,
            'average_speed_kmh': round(avg_speed_kmh, 1),
            'congestion_level_percent': round(congestion_lvl_percent, 1),
            'is_congested': is_congested,
            'vehicles_per_lane': self.lane_counts.copy(), # Return a copy
            'high_density_lanes': high_density_lanes,
            'vehicle_type_counts': vehicle_type_counts
        }

# Example of how this class might be used (optional, for testing this module directly)
if __name__ == '__main__':
    # Dummy config for testing
    sample_config = {
        "speed_limit": 50.0,
        "incident_detection": {
            "density_threshold": 5, # Lower for easier testing
            "congestion_speed_threshold": 15.0
        },
        "stopped_speed_threshold_kmh": 3.0
    }

    monitor = TrafficMonitor(sample_config)

    # Dummy vehicle data
    vehicles_data = {
        1: {"speed": 10.0, "lane": 1, "class_id": 2}, # car, stopped
        2: {"speed": 60.0, "lane": 1, "class_id": 7}, # truck, speeding
        3: {"speed": 30.0, "lane": 2, "class_id": 2}, # car
        4: {"speed": 2.0,  "lane": 1, "class_id": 3}, # motorcycle, stopped
        5: {"speed": 12.0, "lane": 1, "class_id": 5}, # bus, congesting speed
        6: {"speed": 10.0, "lane": 1, "class_id": 2}, # car, congesting speed
        7: {"speed": 8.0,  "lane": 1, "class_id": 7}  # truck, congesting speed
    }

    monitor.update_vehicles(vehicles_data)
    metrics = monitor.get_metrics()

    logger.info("Traffic Monitor Test Metrics:")
    for key, value in metrics.items():
        logger.info(f"  {key}: {value}")

    # Test with no vehicles
    monitor.update_vehicles({})
    metrics_empty = monitor.get_metrics()
    logger.info("Traffic Monitor Test Metrics (Empty):")
    for key, value in metrics_empty.items():
        logger.info(f"  {key}: {value}")
