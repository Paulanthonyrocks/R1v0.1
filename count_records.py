
import sqlite3
import os

db_path = "backend/data/vehicle_data.db"
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    tables = ["vehicle_tracks", "identified_vehicles", "alerts", "incidents"]
    for table in tables:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {table}")
            count = cursor.fetchone()[0]
            print(f"Table '{table}': {count} records")
        except sqlite3.OperationalError:
            print(f"Table '{table}' does not exist.")
            
    conn.close()
else:
    print(f"Database file not found at {db_path}")
