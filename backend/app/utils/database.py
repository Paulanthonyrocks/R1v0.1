File unchanged since last read. The content from the earlier read_file result in this conversation is still current — refer to that instead of re-reading.
    async def get_location_metrics(self, location_id: str, hours: int) -> List[Dict]:
        """Retrieves pre-aggregated location metrics for a specific location and time window."""
        # 1. Try TimescaleDB first if enabled
        if self.timescale_url:
            try:
                async with self.timescale_engine.connect() as conn:
                    start_time = datetime.now(timezone.utc) - timedelta(hours=hours)
                    result = await conn.execute(
                        text("SELECT location_id, timestamp, vehicle_count, average_speed, congestion_score, latitude, longitude "
                             "FROM location_metrics WHERE location_id = :loc_id AND timestamp >= :start_time ORDER BY timestamp DESC"),
                        {"loc_id": location_id, "start_time": start_time}
                    )
                    rows = result.fetchall()
                    if rows:
                        return [dict(row._mapping) for row in rows]
            except Exception as e:
                logger.warning(f"TimescaleDB fetch for location_metrics failed: {e}")
        
        # 2. Fallback to SQLite
        if not self.sqlite_db_path:
            return []
        
        import time
        threshold = time.time() - (hours * 3600)
        query = "SELECT * FROM location_metrics WHERE location_id = ? AND timestamp >= ? ORDER BY timestamp DESC"
        params = (location_id, threshold)
        
        try:
            return await asyncio.to_thread(self._execute_query, query, params)
        except Exception as e:
            logger.error(f"SQLite fetch for location metrics for {location_id} failed: {e}")
            return []
