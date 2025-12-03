import sys
import os
import time
import threading
import multiprocessing
import logging

# Add backend to sys.path
sys.path.append(os.path.abspath("backend"))

from app.core.processing_worker import process_video
from queue import Queue

# Mock MPQueue with standard Queue for simple testing
class MockQueue:
    def __init__(self):
        self.q = Queue()
    def put(self, item):
        self.q.put(item)
    def get_nowait(self):
        return self.q.get_nowait()
    def empty(self):
        return self.q.empty()
    def full(self):
        return self.q.full()

def run_debug():
    logging.basicConfig(level=logging.INFO)
    
    video_path = "backend/data/sample_traffic.mp4"
    frame_queue = MockQueue()
    stop_event = threading.Event()
    alerts_queue = MockQueue()
    
    config = {
        "logging": {"level": "INFO"},
        "video_processing": {"target_fps": 0}, # Max speed
        "video_input": {"max_queue_size": 200},
        "vehicle_detection": {
            "skip_frames": 1,
            "confidence_threshold": 0.4,
            "proximity_threshold": 50,
            "track_timeout": 20,
            "model_path": "backend/models/yolov8n.pt",
             "model_type": "yolo"
        },
        "video_output": {
            "processing_enabled": False, # Disable heavy processing for this test
            "stream_resolution": [640, 480]
        },
        "fps": 30,
        "ocr_engine": {"gemini_api_key": "test"}
    }
    
    feed_id = "debug_feed"
    confidence_threshold = 0.4
    proximity_threshold = 50
    track_timeout = 20
    vis_options = set()
    reduce_frame_rate_event = threading.Event()
    global_fps = None
    
    # Create a thread to run process_video so we can monitor it
    t = threading.Thread(target=process_video, args=(
        video_path, frame_queue, stop_event, alerts_queue,
        config, feed_id, confidence_threshold, proximity_threshold,
        track_timeout, vis_options, reduce_frame_rate_event,
        global_fps
    ), kwargs={"is_looped": False}) # Test without looping first
    
    t.start()
    
    # Monitor output
    count = 0
    start_time = time.time()
    try:
        while t.is_alive():
            if not frame_queue.empty():
                frame_queue.get_nowait()
                count += 1
                if count % 100 == 0:
                    print(f"Processed {count} frames...")
            time.sleep(0.001)
    except KeyboardInterrupt:
        stop_event.set()
    
    t.join()
    print(f"Finished. Total frames processed: {count}")

if __name__ == "__main__":
    run_debug()
