
import time
import sys
import os

# Add backend directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.utils.video import FrameTimer

def test_frametimer():
    timer = FrameTimer()
    
    # Simulate a loop
    time.sleep(0.1)
    timer.tick()
    
    time.sleep(0.1)
    timer.tick()
    
    fps = timer.get_fps("loop_total")
    print(f"FPS: {fps}")
    
    # Expect FPS to be around 10
    if 9.0 < fps < 11.0:
        print("Test Passed")
    else:
        print("Test Failed: FPS out of expected range")

if __name__ == "__main__":
    test_frametimer()
