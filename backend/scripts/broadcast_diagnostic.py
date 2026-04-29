#!/usr/bin/env python3
"""
Broadcast Diagnostic Script
Checks if video frames are being processed and broadcasted correctly.
"""

import sys
import os
import time
sys.path.insert(0, os.path.abspath('.'))

print("=" * 80)
print("BROADCAST DIAGNOSTIC REPORT")
print("=" * 80)
print(f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")
print()

# Check 1: Import test
print("[1/6] Checking imports...")
try:
    from app.services.feed_manager import FeedManager
    from app.websocket.connection_manager import ConnectionManager
    print("  ✓ All imports successful")
except Exception as e:
    print(f"  ✗ Import failed: {e}")
    sys.exit(1)

# Check 2: Check if FeedManager has enhanced logging
print("\n[2/6] Checking for enhanced logging in FeedManager...")
import inspect
source = inspect.getsource(FeedManager._broadcast_video_frame)
if "[BROADCAST]" in source:
    print("  ✓ Enhanced logging detected in _broadcast_video_frame")
else:
    print("  ✗ Enhanced logging NOT found in _broadcast_video_frame")

# Check 3: Check ConnectionManager
print("\n[3/6] Checking for enhanced logging in ConnectionManager...")
source = inspect.getsource(ConnectionManager.broadcast_to_feed_realtime_bytes)
if "[CONN_MGR]" in source:
    print("  ✓ Enhanced logging detected in broadcast_to_feed_realtime_bytes")
else:
    print("  ✗ Enhanced logging NOT found in broadcast_to_feed_realtime_bytes")

# Check 4: Check result reader
print("\n[4/6] Checking result reader loop...")
source = inspect.getsource(FeedManager._result_reader_loop)
if "[RESULT_READER]" in source:
    print("  ✓ Enhanced logging detected in _result_reader_loop")
else:
    print("  ✗ Enhanced logging NOT found in _result_reader_loop")

# Check 5: Check config
print("\n[5/6] Checking configuration...")
try:
    from app.config import initialize_config
    config_path = "backend/app/configs/config.yaml"
    if os.path.exists(config_path):
        config = initialize_config(config_path)
        video_fps = config.video_output.fps if hasattr(config, 'video_output') else 'N/A'
        print(f"  ✓ Config loaded successfully")
        print(f"    - Video FPS: {video_fps}")
    else:
        print(f"  ⚠ Config file not found at {config_path}")
except Exception as e:
    print(f"  ⚠ Config check failed: {e}")

# Check 6: Summary of what to look for in logs
print("\n[6/6] Log Monitoring Guide")
print("-" * 80)
print("When the backend is running, watch for these log patterns:")
print()
print("  1. Frame Reception:")
print("     [RESULT_READER] Received frame X for feed=...")
print()
print("  2. Broadcast Scheduling:")
print("     [BROADCAST] Frame X: now=..., last_broadcast=..., min_interval=...")
print("     [BROADCAST] Scheduling broadcast for feed=... frame=X")
print()
print("  3. Broadcast Execution:")
print("     [BROADCAST] >>>>>> START frame=X feed=...")
print("     [BROADCAST] ConnectionManager: active_connections=...")
print("     [BROADCAST] Payload prepared: type=...")
print("     [BROADCAST] Serialized to X bytes")
print("     [BROADCAST] Broadcasting to feed=...")
print()
print("  4. Connection Manager:")
print("     [CONN_MGR] >>>>>> broadcast_to_feed_realtime_bytes feed=...")
print("     [CONN_MGR] Found X subscribed clients: [...]")
print("     [CONN_MGR] Enqueued frame X for client ...")
print()
print("  5. Success/Failure:")
print("     [BROADCAST] <<<<<< SUCCESS frame=X")
print("     OR")
print("     [BROADCAST] EXCEPTION: ...")
print()
print("=" * 80)
print("DIAGNOSTIC COMPLETE")
print("=" * 80)
print()
print("Next steps:")
print("  1. Start the backend server")
print("  2. Start a video feed")
print("  3. Watch the logs for the patterns above")
print("  4. Check if frames are being received and broadcast")
print("  5. Verify frontend WebSocket connections")
print()
