import logging
import asyncio
import numpy as np
import pandas as pd
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
from app.utils.database import DatabaseManager

logger = logging.getLogger("app.services.analytics_pro")

class AdvancedAnalyticsService:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager

    async def get_origin_destination_matrix(self, start_time: float, end_time: float) -> Dict[str, Any]:
        """
        Calculates the O-D matrix based on vehicles tracked across multiple feeds.
        """
        sql = """
        SELECT global_vehicle_id, feed_id, MIN(timestamp) as first_seen, MAX(timestamp) as last_seen
        FROM vehicle_tracks
        WHERE timestamp BETWEEN ? AND ? AND global_vehicle_id IS NOT NULL
        GROUP BY global_vehicle_id, feed_id
        ORDER BY global_vehicle_id, first_seen
        """
        try:
            rows = await asyncio.to_thread(self.db._execute_query, sql, (start_time, end_time))
            if not rows:
                return {"matrix": {}, "metadata": {"total_vehicles": 0}}

            df = pd.DataFrame(rows)
            
            # For each vehicle, identify the sequence of feeds it visited
            od_pairs = []
            for gid, group in df.groupby("global_vehicle_id"):
                if len(group) >= 2:
                    feeds = group.sort_values("first_seen")["feed_id"].tolist()
                    # We consider each transition as an O-D pair
                    for i in range(len(feeds) - 1):
                        od_pairs.append((feeds[i], feeds[i+1]))

            if not od_pairs:
                return {"matrix": {}, "metadata": {"total_vehicles": len(df["global_vehicle_id"].unique())}}

            # Aggregate into a matrix (pivot table style)
            od_df = pd.DataFrame(od_pairs, columns=["origin", "destination"])
            matrix = od_df.groupby(["origin", "destination"]).size().unstack(fill_value=0)
            
            return {
                "matrix": matrix.to_dict(),
                "metadata": {
                    "total_tracked_vehicles": len(df["global_vehicle_id"].unique()),
                    "total_transitions": len(od_pairs),
                    "start_time": datetime.fromtimestamp(start_time).isoformat(),
                    "end_time": datetime.fromtimestamp(end_time).isoformat()
                }
            }
        except Exception as e:
            logger.error(f"Error calculating O-D matrix: {e}", exc_info=True)
            return {"error": str(e)}

    async def get_average_travel_times(self, start_time: float, end_time: float) -> List[Dict[str, Any]]:
        """
        Calculates average travel time between feed pairs.
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
            travel_data = []

            for gid, group in df.groupby("global_vehicle_id"):
                if len(group) >= 2:
                    sorted_group = group.sort_values("entry_time")
                    for i in range(len(sorted_group) - 1):
                        origin = sorted_group.iloc[i]
                        dest = sorted_group.iloc[i+1]
                        
                        # Travel time from exit of origin to entry of destination
                        duration = dest["entry_time"] - origin["exit_time"]
                        if 0 < duration < 3600: # Filter out noise (e.g., parked for hours)
                            travel_data.append({
                                "from": origin["feed_id"],
                                "to": dest["feed_id"],
                                "duration": duration
                            })

            if not travel_data: return []

            travel_df = pd.DataFrame(travel_data)
            summary = travel_df.groupby(["from", "to"])["duration"].agg(["mean", "count", "min", "max"]).reset_index()
            
            return summary.to_dict("records")
        except Exception as e:
            logger.error(f"Error calculating travel times: {e}")
            return []

    async def get_heatmap_data(self, feed_id: Optional[str] = None, global_id: Optional[str] = None, hours: int = 1) -> List[Dict[str, Any]]:
        """
        Generates point-based heatmap data. Prioritizes TimescaleDB if available.
        """
        # --- 1. TRY TIMESCALEDB (Optimized for time-range) ---
        if self.db.timescale_engine:
            try:
                from sqlalchemy import text
                start_time = datetime.now(timezone.utc) - timedelta(hours=hours)
                
                query = "SELECT center_x, center_y FROM vehicle_tracks WHERE timestamp > :start"
                params = {"start": start_time}
                
                if feed_id:
                    query += " AND feed_id = :fid"
                    params["fid"] = feed_id
                if global_id:
                    query += " AND global_vehicle_id = :gid"
                    params["gid"] = global_id
                
                async with self.db.timescale_engine.connect() as conn:
                    result = await conn.execute(text(query), params)
                    return [dict(row._mapping) for row in result]
            except Exception as e:
                logger.error(f"TimescaleDB heatmap query failed, falling back to SQLite: {e}")

        # --- 2. FALLBACK TO SQLITE ---
        start_time_unix = time.time() - (hours * 3600)
        sql = "SELECT center_x, center_y FROM vehicle_tracks WHERE timestamp > ?"
        params = [start_time]
        
        if feed_id:
            sql += " AND feed_id = ?"
            params.append(feed_id)
            
        if global_id:
            sql += " AND global_vehicle_id = ?"
            params.append(global_id)
            
        try:
            rows = await asyncio.to_thread(self.db._execute_query, sql, tuple(params))
            return rows
        except Exception as e:
            logger.error(f"Error getting heatmap data: {e}")
            return []
