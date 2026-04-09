# ... (all other imports and code remain the same)
    def _save_vehicle_data(self, tracked_vehicles: Dict[str, Dict]):
        from ..utils.encryption import encryption_manager
        now = time.time()
        for vid, data in tracked_vehicles.items():
            if (now - self._last_save_time.get(vid, 0)) < 0.5: continue # Rate limit DB writes per vehicle
            if self.db_queue and data.get("status") == "active":
                try: 
                    # Fix: Encrypt license plate PII before sending to DB queue
                    if "license_plate" in data:
                        data["license_plate"] = encryption_manager.encrypt(data["license_plate"])
                    
                    # FIX: Flatten the structure to match the format expected by DatabaseManager.save_vehicle_data_batch
                    track_data = data.copy()
                    track_data["type"] = "vehicle_data"
                    track_data["feed_id"] = self.feed_id
                    
                    self.db_queue.put_nowait(track_data) 
                    self._last_save_time[vid] = now
                except queue.Full: pass # Non-critical, can be dropped
# ... (rest of the file remains the same)
