# ... (lines 1-744)
                for item in items_buffer:
                    try:
                        if not isinstance(item, (list, tuple)) or len(item) != 6:
                            logger.warning(f"Received malformed inference result: {item}. Expected 6 elements, got {len(item) if isinstance(item, (list, tuple)) else 'not a sequence'}")
                            continue
                        
                        feed_id, frame_idx, frame_bytes, metrics, vehicles, extra = item
                    except Exception as e:
                        logger.error(f"Error unpacking inference result: {e}")
                        continue
                        
                    entry = self.process_registry.get(feed_id)
# ... (rest of the file remains the same)
