import logging
import asyncio
import time
import pandas as pd
from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta, timezone
from app.utils.database import DatabaseManager
from app.utils.json_sanitize import to_jsonable
from sqlalchemy import text

logger = logging.getLogger("app.services.analytics_pro")

class AdvancedAnalyticsService:
    def __init__(self, db_manager: DatabaseManager):
        self.db = db_manager
        # Placement wiring for road-keyed OD/heatmap (set by ServiceContainer;
        # None keeps those endpoints falling back to feed-id keys).
        self._feed_manager = None
        self._road_service = None

    def set_feed_manager(self, feed_manager):
        self._feed_manager = feed_manager

    def set_road_service(self, road_service):
        self._road_service = road_service

    def _feed_geo_snaps(self) -> Dict[str, Optional[Dict[str, Any]]]:
        """{feed_id: describe_snap(...)} for every placed feed (sync, cheap:
        one snap per feed, not per track)."""
        out: Dict[str, Optional[Dict[str, Any]]] = {}
        if not (self._feed_manager and self._road_service):
            return out
        for fid, entry in (self._feed_manager.process_registry or {}).items():
            cfg = entry.get("config_info") if isinstance(entry, dict) else None
            if cfg is None or getattr(cfg, "latitude", None) is None:
                continue
            try:
                out[fid] = self._road_service.describe_snap(
                    float(cfg.latitude), float(cfg.longitude),
                    road_id=getattr(cfg, "road_id", None),
                )
            except Exception:
                out[fid] = None
        return out

    async def get_road_od_matrix(
        self, start_time: float, end_time: float, bucket_m: float = 100.0
    ) -> Dict[str, Any]:
        """
        OD matrix keyed by road corridor position ("main-st@400m" ->
        "main-st@900m") instead of raw feed ids. Same vehicle_tracks query as
        get_origin_destination_matrix; plain-python aggregation (no pandas) so
        it also runs where pandas is absent. Feeds without placement keep
        "feed:<id>" keys.
        """
        from app.utils.road_od import build_road_od_matrix
        sql = """
        SELECT global_vehicle_id, feed_id, MIN(timestamp) as entry_time
        FROM vehicle_tracks
        WHERE timestamp BETWEEN ? AND ? AND global_vehicle_id IS NOT NULL
        GROUP BY global_vehicle_id, feed_id
        ORDER BY global_vehicle_id, entry_time
        """
        try:
            rows = await asyncio.to_thread(self.db._execute_query, sql, (start_time, end_time))
            transitions = []
            last_by_vehicle: Dict[str, str] = {}
            vehicles = set()
            for row in rows or []:
                gid = row["global_vehicle_id"] if isinstance(row, dict) else row[0]
                fid = row["feed_id"] if isinstance(row, dict) else row[1]
                vehicles.add(gid)
                prev = last_by_vehicle.get(gid)
                if prev is not None and prev != fid:
                    transitions.append((prev, fid))
                last_by_vehicle[gid] = fid
            result = build_road_od_matrix(transitions, self._feed_geo_snaps(), bucket_m)
            result["metadata"].update({
                "total_tracked_vehicles": len(vehicles),
                "start_time": datetime.fromtimestamp(start_time, timezone.utc).isoformat(),
                "end_time": datetime.fromtimestamp(end_time, timezone.utc).isoformat(),
            })
            return result
        except Exception as e:
            logger.error(f"Error calculating road OD matrix: {e}", exc_info=True)
            return {"error": str(e)}

    async def get_heatmap_road_data(
        self,
        feed_id: Optional[str] = None,
        hours: int = 1,
        max_points: int = 5000,
    ) -> List[Dict[str, Any]]:
        """
        Heatmap points (pixel centers from vehicle_tracks) projected to WGS84
        through each feed's geo homography, then snapped to the road registry:
        each point gains road_id/road_name/chainage_m/road_bearing_deg (None
        when unplaced). Capped at max_points (evenly strided).
        """
        from app.utils.road_od import annotate_heatmap_point
        if not (self._feed_manager and self._road_service):
            return []
        try:
            start_ts = time.time() - hours * 3600
            sql = (
                "SELECT feed_id, center_x, center_y FROM vehicle_tracks "
                "WHERE timestamp > ?"
            )
            params = (start_ts,)
            if feed_id:
                sql += " AND feed_id = ?"
                params = (start_ts, feed_id)
            rows = await asyncio.to_thread(self.db._execute_query, sql, params)
            rows = rows or []
            if len(rows) > max_points:
                stride = len(rows) / max_points
                rows = [rows[int(i * stride)] for i in range(max_points)]
            out: List[Dict[str, Any]] = []
            for row in rows:
                fid = row["feed_id"] if isinstance(row, dict) else row[0]
                cx = row["center_x"] if isinstance(row, dict) else row[1]
                cy = row["center_y"] if isinstance(row, dict) else row[2]
                if cx is None or cy is None:
                    continue
                entry = (self._feed_manager.process_registry or {}).get(fid) or {}
                H = entry.get("geo_homography") if isinstance(entry, dict) else None
                geo = None
                if H is not None:
                    from app.utils.geo_calibration import project_geo
                    geo = project_geo(H, float(cx), float(cy))
                if geo is None:
                    continue  # uncalibrated feed: no WGS84 point to key
                lat, lng = geo
                pt = {"feed_id": fid, "latitude": lat, "longitude": lng}
                pt.update(annotate_heatmap_point(lat, lng, self._road_service))
                out.append(pt)
            return out
        except Exception as e:
            logger.error(f"Error getting road heatmap data: {e}", exc_info=True)
            return []

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
                "matrix": to_jsonable(matrix.to_dict()),
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
            return to_jsonable(summary.rename(columns={'feed_id': 'from', 'next_feed': 'to'}).to_dict("records"))

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