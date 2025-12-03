## Bug Fix: KPI Data Not Populating on Dashboard - (2025-11-28)

**Summary:**
Addressed an issue where global KPI data was not being populated or correctly displayed on the frontend DashboardPage. The problem was primarily due to a mismatch between the frontend's `KPIData` interface and the backend's `GlobalRealtimeMetrics` model, leading to incorrect data retrieval.

**Key Activities:**
- **Identified Data Mismatch:** The frontend's `KPIData` interface in `frontend/lib/hook/useRealtimeUpdates.ts` contained fields (`vehicles`, `vehicle_count`, `avg_speed`) that were not present in the `GlobalRealtimeMetrics` object sent by the backend for global KPI updates. Conversely, some backend fields (`timestamp`, `metrics_source`, `feed_statuses`) were missing from the frontend interface.
- **Frontend Interface Alignment:**
    - Modified the `KPIData` interface in `frontend/lib/hook/useRealtimeUpdates.ts` to accurately reflect all fields from the backend's `GlobalRealtimeMetrics` model, removing irrelevant fields and adding missing ones.
- **Frontend Data Access Correction:**
    - Adjusted `frontend/app/dashboard/page.tsx` to directly reference `kpis?.average_speed_kmh` and `kpis?.total_flow` for the respective KPI values, removing fallbacks to `kpis?.avg_speed` and `kpis?.vehicle_count`, which are no longer part of the global KPI data structure.

**Changes Made:**
- **Files Modified:**
    - `frontend/lib/hook/useRealtimeUpdates.ts`
    - `frontend/app/dashboard/page.tsx`

**Technical Decisions:**
- Ensuring strict type alignment between frontend interfaces and backend models is crucial for reliable data flow and display. Correcting these mismatches ensures that the DashboardPage now properly receives and displays global KPI data.
- The default `_kpi_broadcast_interval` of 1 second in the backend's `FeedManager` ensures timely updates.

**Current Status:**
- ✅ KPI data should now correctly populate on the DashboardPage.

## Bug Fix: Video Processing Premature Exit - (2025-12-02)

**Summary:**
Fixed a bug where the video processing pipeline would exit prematurely, processing only ~175 frames of a 1275-frame video. The issue was traced to the explicit usage of `cv2.CAP_FFMPEG` in the `FrameReader` class, which caused read failures in the application context.

**Key Activities:**
- **Diagnosis:** Investigated `backend/app/utils/video.py` and found `cv2.VideoCapture` was forcing `cv2.CAP_FFMPEG`.
- **Verification:** Confirmed that `verify_video.py` (using default backend) could read all frames.
- **Fix:** Removed `cv2.CAP_FFMPEG` from `backend/app/utils/video.py` to allow OpenCV to automatically select the best backend (usually FFMPEG on Linux, but with better compatibility).

**Changes Made:**
- **Files Modified:** `backend/app/utils/video.py` (removed `cv2.CAP_FFMPEG` argument).

**Technical Decisions:**
- Allowing OpenCV to auto-detect the backend (`cv2.CAP_ANY` behavior) is generally more robust than forcing a specific backend unless a specific feature is required. This resolves the premature termination issue.

**Current Status:**
- ✅ Video processing should now complete all frames.

**Next Steps:**
- Verify KPI display on the frontend dashboard.
- Confirm that sample feeds are running and producing metrics, as empty feeds would lead to zero/null KPIs.