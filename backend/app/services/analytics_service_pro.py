import logging
import asyncio
import time
import pandas as pd
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta, timezone
from app.utils.database import DatabaseManager
from sqlalchemy import text

logger = logging.getLogger("app.services.analytics_pro")

class AdvancedAnalyticsService:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    async def get_origin_destination_matrix(self, start_time: float, end_time: float) -> Dict[str, Any]:
        """
        Calculates O-D matrix using vectorized Pandas operations (No loops).
        """
        # 1. Get Data (Aggregated by vehicle/feed to reduce row count)
        sql = """
        SELECT global_vehicle_id, feed_id, MIN(timestamp) as entry_time
        FROM vehicle_tracks
        WHERE timestamp BETWEEN ? AND ? AND global_vehicle_id IS NOT NULL
        GROUP BY global_vehicle_id, feed_id
        """
        try:
            rows = await asyncio.to_thread(self.db._execute_query, sql, (start_time, end_time))
            if not rows:
                return {"matrix": {}, "metadata": {"total_vehicles": 0}}

            df = pd.DataFrame(rows)
            
            # 2. Sort by Vehicle and Time
            df = df.sort_values(by=["global_vehicle_id", "entry_time"])

            # 3. Vectorized Shift: Get the 'next' feed for every row
            df['next_feed'] = df.groupby('global_vehicle_id')['feed_id'].shift(-1)
            
            # 4. Filter: We only care about rows where next_feed exists and is different
            transitions = df.dropna(subset=['next_feed'])
            transitions = transitions[transitions['feed_id'] != transitions['next_feed']]

            if transitions.empty:
                 return {"matrix": {}, "metadata": {"total_vehicles": int(df["global_vehicle_id"].nunique())}}

            # 5. Crosstab (Pivot) - Instant count
            matrix = pd.crosstab(transitions['feed_id'], transitions['next_feed'])
            
            return {
                "matrix": matrix.to_dict(),
                "metadata": {
                    "total_tracked_vehicles": int(df["global_vehicle_id"].nunique()),
                    "total_transitions": len(transitions),
                    "start_time": datetime.fromtimestamp(start_time, timezone.utc).isoformat(),
                    "end_time": datetime.fromtimestamp(end_time, timezone.utc).isoformat()
                }
            }
        except Exception as e:
            logger.error(f"Error calculating O-D matrix: {e}", exc_info=True)
            return {"error": str(e)}

    async def get_average_travel_times(self, start_time: float, end_time: float) -> List[Dict[str, Any]]:
        """
        Calculates travel times using vectorization.
        """
        sql = """
        SELECT global_vehicle_id, feed_id, MAX(timestamp) as exit_time, MIN(timestamp) as entry_time
        FROM vehicle_tracks
        WHERE timestamp BETWEEN ? AND ? AND global_vehicle_id IS NOT NULL
        GROUP BY global_vehicle_id, feed_id
        """
        try:
            rows = await asyncio.to_thread(self.db._execute_query, sql, (start_time, end_time))
            if not rows: return []

            df = pd.DataFrame(rows)
            df = df.sort_values(by=["global_vehicle_id", "entry_time"])

            # 1. Shift to find next entry time and next feed
            df['next_entry_time'] = df.groupby('global_vehicle_id')['entry_time'].shift(-1)
            df['next_feed'] = df.groupby('global_vehicle_id')['feed_id'].shift(-1)

            # 2. Calculate Duration (Next Entry - Current Exit)
            df['duration'] = df['next_entry_time'] - df['exit_time']

            # 3. Filter valid transitions
            valid = df.dropna(subset=['duration', 'next_feed'])
            valid = valid[valid['feed_id'] != valid['next_feed']]
            valid = valid[(valid['duration'] > 0) & (valid['duration'] < 3600)] # 1 hour max travel

            if valid.empty: return []

            # 4. Group by Route
            summary = valid.groupby(['feed_id', 'next_feed'])['duration'].agg(
                average_seconds='mean',
                trip_count='count',
                min_seconds='min',
                max_seconds='max'
            ).reset_index()

            # Rename columns to match expected output
            return summary.rename(columns={'feed_id': 'from', 'next_feed': 'to'}).to_dict("records")

        except Exception as e:
            logger.error(f"Error calculating travel times: {e}", exc_info=True)
            return []

    async def get_heatmap_data(self, feed_id: Optional[str] = None, global_id: Optional[str] = None, hours: int = 1) -> List[Dict[str, Any]]:
        """
        Generates point-based heatmap data. Handles Timestamps correctly.
        """
        try:
            # --- 1. TRY TIMESCALEDB ---
            if self.db.timescale_engine:
                start_dt = datetime.now(timezone.utc) - timedelta(hours=hours)
                
                query_str = "SELECT center_x, center_y FROM vehicle_tracks WHERE timestamp > :start"
                params = {"start": start_dt}
                
                if feed_id:
                    query_str += " AND feed_id = :fid"
                    params["fid"] = feed_id
                if global_id:
                    query_str += " AND global_vehicle_id = :gid"
                    params["gid"] = global_id
                
                # Use a specific try block for Timescale connection
                try:
                    async with self.db.timescale_engine.connect() as conn:
                        result = await conn.execute(text(query_str), params)
                        return [dict(row._mapping) for row in result]
                except Exception as ex:
                    logger.warning(f"TimescaleDB heatmap query failed, falling back: {ex}")

            # --- 2. FALLBACK TO SQLITE ---
            # Correctly calculate Unix timestamp for SQLite
            start_unix = time.time() - (hours * 3600)
            
            sql = "SELECT center_x, center_y FROM vehicle_tracks WHERE timestamp > ?"
            sql_params = [start_unix]
            
            if feed_id:
                sql += " AND feed_id = ?"
                sql_params.append(feed_id)
                
            if global_id:
                sql += " AND global_vehicle_id = ?"
                sql_params.append(global_id)
                
            rows = await asyncio.to_thread(self.db._execute_query, sql, tuple(sql_params))
            return rows

        except Exception as e:
            logger.error(f"Error getting heatmap data: {e}", exc_info=True)
            return []