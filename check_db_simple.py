
import sqlite3
import os

db_path = "backend/data/vehicle_data.db"
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='identified_vehicles';")
    result = cursor.fetchone()
    if result:
        print("Table 'identified_vehicles' exists.")
        # Check columns
        cursor.execute("PRAGMA table_info(identified_vehicles);")
        columns = cursor.fetchall()
        for col in columns:
            print(f"Column: {col[1]} ({col[2]})")
    else:
        print("Table 'identified_vehicles' DOES NOT exist.")
    conn.close()
else:
    print(f"Database file not found at {db_path}")
