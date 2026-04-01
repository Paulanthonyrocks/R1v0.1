import pickle
import multiprocessing
import sys
import os

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from app.utils.distributed_queue import RedisQueue
from app.config import get_current_config

def test_worker(q):
    print("Worker started")
    try:
        item = q.get(timeout=1)
        print(f"Worker received: {item}")
    except Exception as e:
        print(f"Worker error: {e}")

if __name__ == "__main__":
    # Ensure redis is enabled in config for the test
    cfg = get_current_config()
    if not cfg.redis.enabled:
        print("Redis is not enabled in config. Skipping test.")
        sys.exit(0)

    q = RedisQueue("test_pickle_queue")
    
    # This will initialize the _redis attribute
    print("Calling clear()...")
    try:
        q.clear()
        print("clear() successful.")
    except Exception as e:
        print(f"clear() failed (is Redis running?): {e}")
        # Even if it fails, _redis might be set if it tried to connect
    
    print(f"q._redis is: {q._redis}")
    
    # Try to pickle it
    print("Attempting to pickle RedisQueue...")
    try:
        p_bytes = pickle.dumps(q)
        print("Pickling successful.")
        
        # Try to start a process with it
        print("Starting process with 'spawn' method...")
        multiprocessing.set_start_method('spawn', force=True)
        p = multiprocessing.Process(target=test_worker, args=(q,))
        p.start()
        
        # Put something in the queue (this will re-initialize _redis)
        q.put("hello from main")
        
        p.join()
        print("Process finished.")
    except Exception as e:
        print(f"Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
