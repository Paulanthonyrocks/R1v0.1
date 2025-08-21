## DINOv3 Integration Plan Draft - (2025-08-14)

**Summary:**
Draft plan for integrating DINOv3 as a powerful backbone for object detection in the traffic monitoring system, replacing or augmenting the current YOLOv8n implementation.

**Key Activities:**
- Research DINOv3 architectures and suitable detection heads.
- Acquire pre-trained models.
- Adapt and fine-tune the model with traffic-specific data.
- Export the model to ONNX.
- Integrate into the backend ML service and API.
- Conduct thorough testing, evaluation, and optimization.

**Changes Made:**
- This plan is being saved to `memory.md` for future reference.

**Technical Decisions:**
- DINOv3 will be used as a feature extractor/backbone, requiring a separate detection head.
- ONNX export is crucial for deployment efficiency.

**Current Status:**
- ✅ Draft plan created and saved.

**Next Steps:**
- Begin Phase 1: Research and Model Selection, focusing on specific DINOv3 variants and compatible detection heads.
- Identify potential datasets for fine-tuning.

**Open Questions/Challenges:**
- Specific DINOv3 variant and detection head to use.
- Availability of pre-trained models and training code.
- Data requirements for fine-tuning on traffic-specific scenarios.

**Quality Assurance Checklist:**
- [ ] Plan reviewed for completeness and feasibility.

**Integration Points:**
- Backend ML service (`backend/app/ml/`).
- Backend models directory (`backend/models/`).
- FastAPI routers (`backend/app/routers/`).

## Resolved Sample Video Path Warning - (2025-08-14)

**Summary:**
Addressed the "Sample video path configured but not found" warning in the application logs by commenting out the `sample_video` entry in `backend/configs/config.yaml`.

**Key Activities:**
- Identified the warning message in the provided log snippet.
- Located the `sample_video` configuration in `backend/configs/config.yaml` using `search_file_content`.
- Confirmed the absence of `sample_traffic.mp4` in the `backend/data` directory.
- Commented out the `sample_video` line in `backend/configs/config.yaml` to prevent the application from looking for the missing file.

**Changes Made:**
- **Files Modified:** `backend/configs/config.yaml` (commented out `sample_video` line)

**Technical Decisions:**
- Chose to comment out the configuration entry as the `sample_video` appears to be optional and not critical for core application functionality. This avoids the warning without introducing new files or complex changes.

**Current Status:**
- ✅ Warning resolved.

**Next Steps:**
- None, this task is complete.

**Open Questions/Challenges:**
- None.

**Quality Assurance Checklist:**
- [ ] Change implemented and verified to resolve the warning.
- [ ] No new issues introduced.

## Re-enabled Sample Video Path - (2025-08-14)

**Summary:**
Re-enabled the `sample_video` path in `backend/configs/config.yaml` after the user provided the full path and the file was confirmed to exist.

**Key Activities:**
- User provided the full path to `sample_traffic.mp4`.
- Confirmed the existence of the file at the provided path using `read_file` (which failed due to file size, but confirmed existence).
- Uncommented the `sample_video` line in `backend/configs/config.yaml`.

**Changes Made:**
- **Files Modified:** `backend/configs/config.yaml` (uncommented `sample_video` line)

**Technical Decisions:**
- Reverted the previous change to allow the application to use the `sample_traffic.mp4` file, as it is now available.

**Current Status:**
- ✅ `sample_video` path re-enabled.

**Next Steps:**
- None, this task is complete.

**Open Questions/Challenges:**
- None.

**Quality Assurance Checklist:**
- [ ] Change implemented and verified.
- [ ] No new issues introduced.

## Corrected Sample Video Path Resolution - (2025-08-14)

**Summary:**
Corrected the path resolution logic for `sample_video` in `backend/app/services/feed_manager.py` to prevent the duplication of the `backend` directory in the resolved path.

**Key Activities:**
- Identified the duplicated `backend` segment in the resolved path from the log messages.
- Located the path resolution logic in `_initialize_available_feeds` within `backend/app/services/feed_manager.py`.
- Added `project_root_dir` to `backend/configs/config.yaml` to provide a base for path resolution.
- Modified `_initialize_available_feeds` to construct the `resolved_path` using `Path(self.config.get("project_root_dir"), sample_path_str)`.

**Changes Made:**
- **Files Modified:** 
    - `backend/configs/config.yaml` (added `project_root_dir`)
    - `backend/app/services/feed_manager.py` (modified path resolution logic)

**Technical Decisions:**
- Using `project_root_dir` from the configuration ensures that paths are resolved correctly relative to the project's root, preventing issues with relative path interpretation when the current working directory is a subdirectory.

**Current Status:**
- ✅ Path resolution corrected.

**Next Steps:**
- None, this task is complete.

**Open Questions/Challenges:**
- None.

**Quality Assurance Checklist:**
- [ ] Changes implemented and verified.
- [ ] No new issues introduced.

## Decreased FPS for Performance Improvement - (2025-08-14)

**Summary:**
Decreased the `fps` setting in `backend/configs/config.yaml` from `5` to `3` to improve performance and alleviate frame reader queue buildup.

**Key Activities:**
- User reported frame reader queue filling up fast and low frame processing in detection and initialization.
- Explained how decreasing FPS can help by reducing input load and allowing processing stages to catch up.
- Modified the `fps` value in `backend/configs/config.yaml`.

**Changes Made:**
- **Files Modified:** `backend/configs/config.yaml` (changed `fps` from `5` to `3`)

**Technical Decisions:**
- Lowering FPS is a direct way to reduce the processing load and address bottlenecks in the video processing pipeline.

**Current Status:**
- ✅ `fps` value updated.

**Next Steps:**
- User to observe performance changes after restarting the application.

**Open Questions/Challenges:**
- Further performance tuning might be required based on observed results.

**Quality Assurance Checklist:**
- [ ] Change implemented correctly.
- [ ] No new issues introduced.

## Enabled ROI Processing - (2025-08-14)

**Summary:**
Enabled Region of Interest (ROI) processing by setting `roi_processing: enabled` to `true` in `backend/configs/config.yaml`.

**Key Activities:**
- User requested to enable ROI polygon points.
- Identified the `roi_processing: enabled` setting in `config.yaml`.
- Changed the value from `false` to `true`.

**Changes Made:**
- **Files Modified:** `backend/configs/config.yaml` (changed `roi_processing: enabled` from `false` to `true`)

**Technical Decisions:**
- Enabling ROI processing allows the application to focus on specific areas of interest within video frames, potentially improving performance and accuracy for relevant tasks.

**Current Status:**
- ✅ ROI processing enabled.

**Next Steps:**
- Clarify the meaning of "fail reads" from the user to address the second part of their request.

**Open Questions/Challenges:**
- What does "fail reads" refer to in the configuration?

**Quality Assurance Checklist:**
- [ ] Change implemented correctly.
- [ ] No new issues introduced.

## Reduced Max Read Fails - (2025-08-14)

**Summary:**
Reduced the `max_read_fails` threshold in `backend/app/utils/video.py` from `100` to `30` to decrease the number of consecutive failed frame reads before assuming an issue.

**Key Activities:**
- User clarified "fail reads" by providing log messages indicating `cv2.read() returned False` and `Max read fails reached`.
- Searched `backend/app/utils/video.py` and identified `max_read_fails = 100` as the relevant parameter.
- Modified the hardcoded value of `max_read_fails` to `30`.

**Changes Made:**
- **Files Modified:** `backend/app/utils/video.py` (changed `max_read_fails` from `100` to `30`)

**Technical Decisions:**
- Lowering this threshold means the system will more quickly identify and potentially stop processing a problematic video stream, preventing excessive resource consumption on a failing input.

**Current Status:**
- ✅ `max_read_fails` reduced.

**Next Steps:**
- None, this task is complete.

**Open Questions/Challenges:**
- None.

**Quality Assurance Checklist:**
- [ ] Change implemented correctly.
- [ ] No new issues introduced.