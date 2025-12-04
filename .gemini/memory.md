## Fix Overlay Scaling Issues in Surveillance Feed - (2025-12-04)

**Summary:**
Fixed an issue where overlay controls were "failing to reflect" in the feed stream. The root cause was a coordinate scaling mismatch. The backend was sending bounding boxes in original resolution coordinates but streaming a resized image. The frontend was then drawing these coordinates directly onto a canvas that might be further scaled by CSS, resulting in misaligned or invisible overlays.

**Key Activities:**
- Analyzed `SurveillanceFeed.tsx`, `useVideoSocket.ts`, and `video_processor.py` / `processing_worker.py`.
- Identified that bounding boxes were not being scaled to match the streamed JPEG resolution.
- Identified that the frontend was not scaling coordinates to match the canvas display size.

**Changes Made:**
- **Files Modified:**
    - `backend/app/core/processing_worker.py`: Updated `_serialize_tracked_vehicles` and `process_video` to scale vehicle bounding boxes to match the streamed frame resolution (e.g., 640x480).
    - `frontend/lib/useVideoSocket.ts`: Updated `drawFrame` to calculate scale factors (`canvas.width / img.width`) and apply them to bounding boxes before drawing.

**Current Status:**
- ✅ Overlays should now align correctly with the video feed regardless of backend stream resolution or frontend window size.
- ✅ Toggling overlay controls should now correctly show/hide the visible overlays.

**Next Steps:**
- Verify the fix with a live feed if possible.
- Monitor for any performance impact of client-side scaling (should be negligible).
