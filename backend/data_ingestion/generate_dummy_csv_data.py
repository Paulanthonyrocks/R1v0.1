import random
import pandas as pd
from datetime import datetime, timedelta

def generate_dummy_csv_data(num_entries: int = 5000, filename: str = "traffic_data.csv"):
    data = []
    sensors = {
        "SENSOR001": {"latitude": 34.0522, "longitude": -118.2437}, # Los Angeles
        "SENSOR002": {"latitude": 40.7128, "longitude": -74.0060},  # New York
        "SENSOR003": {"latitude": 41.8781, "longitude": -87.6298}   # Chicago
    }
    sensor_ids = list(sensors.keys())

    for i in range(num_entries):
        sensor_id = random.choice(sensor_ids)
        base_coords = sensors[sensor_id]
        
        timestamp = datetime.now() - timedelta(minutes=random.randint(0, 60 * 24 * 7)) # Last 7 days
        
        vehicle_count = random.randint(5, 100)
        average_speed = random.uniform(10, 80)
        congestion_score = min((vehicle_count / average_speed) * 10, 100) if average_speed > 0 else random.uniform(50, 100)
        
        incident_occurred = 0
        if congestion_score > 70 and random.random() < 0.3: # 30% chance of incident if high congestion
            incident_occurred = 1
        elif average_speed < 20 and random.random() < 0.2: # 20% chance if very slow
            incident_occurred = 1

        data.append({
            "sensor_id": sensor_id,
            "timestamp": timestamp,
            "latitude": round(base_coords["latitude"] + random.uniform(-0.01, 0.01), 6),
            "longitude": round(base_coords["longitude"] + random.uniform(-0.01, 0.01), 6),
            "vehicle_count": vehicle_count,
            "average_speed": round(average_speed, 2),
            "congestion_level": round(random.uniform(1, 5), 2),
            "congestion_score": round(congestion_score, 2),
            "processing_timestamp": datetime.now(),
            "status": 'processed',
            "hour_of_day": timestamp.hour,
            "day_of_week": timestamp.weekday(),
            "is_weekend": timestamp.weekday() >= 5,
            "road_type": random.choice(["major_artery", "highway", "local_road"]),
            "weather_conditions_temperature": random.uniform(10, 35),
            "weather_conditions_precipitation": random.uniform(0, 5),
            "truck_percentage": round(random.uniform(0.05, 0.3), 2),
            "is_outlier": False,
            "incident_occurred": incident_occurred
        })

    df = pd.DataFrame(data)
    df.to_csv(filename, index=False)
    print(f"Generated {num_entries} dummy entries and saved to {filename}")

if __name__ == "__main__":
    generate_dummy_csv_data(num_entries=5000, filename="/home/user/R1v0.1/backend/data/traffic_data.csv")