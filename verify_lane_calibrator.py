import sys
import os
import math
import numpy as np

# Add backend to path
sys.path.append(os.path.join(os.getcwd(), "backend"))

from app.services.lane_calibrator import LaneCalibrator

def test_lane_calibration():
    print("Starting LaneCalibrator Verification...")
    calibrator = LaneCalibrator(min_samples=20, confidence_threshold=0.8)
    
    feed_id = "test_feed"
    lane_id = 1
    
    # 1. Test learning a consistent direction (North-East: vx=1, vy=-1)
    print("\nPhase 1: Consistent Direction (NE)")
    for i in range(25):
        # Add some noise to vectors
        vx = 1.0 + np.random.normal(0, 0.1)
        vy = -1.0 + np.random.normal(0, 0.1)
        calibrator.add_sample(feed_id, lane_id, vx, vy)
        
        vector, conf = calibrator.get_flow_vector(feed_id, lane_id)
        if i >= 19: # Should be calibrated after 20 samples
            print(f"Sample {i+1}: Vector={vector}, Confidence={conf:.2f}")
    
    vector, conf = calibrator.get_flow_vector(feed_id, lane_id)
    assert vector is not None, "Failed to converge on a vector"
    assert conf >= 0.8, f"Confidence too low: {conf}"
    
    # Check if normalized vector is roughly [0.707, -0.707]
    assert abs(vector[0] - 0.707) < 0.1
    assert abs(vector[1] + 0.707) < 0.1
    print("✅ Phase 1 Passed: Converged correctly on consistent flow.")

    # 2. Test Random Direction (Should have low confidence)
    print("\nPhase 2: Random Direction")
    lane_id_rand = 2
    for i in range(30):
        vx = np.random.uniform(-1, 1)
        vy = np.random.uniform(-1, 1)
        calibrator.add_sample(feed_id, lane_id_rand, vx, vy)
        
    vector, conf = calibrator.get_flow_vector(feed_id, lane_id_rand)
    print(f"Random Flow: Vector={vector}, Confidence={conf:.2f}")
    assert conf < 0.5, f"Confidence too high for random flow: {conf}"
    print("✅ Phase 2 Passed: Low confidence for random flow.")

    # 3. Test Fallback Logic
    print("\nPhase 3: Fallback Logic")
    # calibrated lane 1 should serve as fallback for lane 3 if lane 3 is unknown
    vector, conf = calibrator.get_flow_vector(feed_id, 3)
    # Wait, my logic skips fallback if feed_id is not in lane_data, but here it is.
    # But it only falls back if lane_id != -1 and -1 in lane_map. 
    # Let's add samples to -1 (global feed flow)
    for i in range(25):
        calibrator.add_sample(feed_id, -1, 0, 1) # South
        
    vector, conf = calibrator.get_flow_vector(feed_id, 5) # Unknown lane 5
    print(f"Fallback Flow for Lane 5: Vector={vector}, Confidence={conf:.2f}")
    assert vector[1] == 1.0, "Fallback failed to use global flow"
    print("✅ Phase 3 Passed: Fallback to global flow works.")

    print("\nAll LaneCalibrator tests PASSED! 🎉")

if __name__ == "__main__":
    test_lane_calibration()
