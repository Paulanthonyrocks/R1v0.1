# Troubleshooting Guide: Traffic Management Hub

This document provides solutions and common troubleshooting steps for issues encountered during the development and operation of the Traffic Management Hub.

## 1. General Troubleshooting Steps

Before diving into specific issues, try these general steps:

- **Check Logs:** Always start by examining the logs for both the frontend and backend services. Look for `ERROR`, `CRITICAL`, or `WARNING` messages.
    - Backend logs: `backend/logs/` (or console output if running in foreground).
    - Frontend logs: Browser console (for client-side errors) or terminal where Next.js is running (for server-side rendering errors).
- **Verify Dependencies:** Ensure all project dependencies are correctly installed.
    - Frontend: Run `npm install` in `frontend/`.
    - Backend: Run `pip install -r backend/requirements.txt` in the project root.
- **Restart Services:** Sometimes, a simple restart can resolve transient issues.
    - Backend: Stop and restart the Uvicorn server.
    - Frontend: Stop and restart the Next.js development server.
- **Check Configuration:** Confirm that all configuration files (`.env`, `config.yaml`, `next.config.js`, etc.) are correctly set up and environment variables are loaded.
- **Network Connectivity:** Ensure that all necessary ports are open and services can communicate with each other (e.g., frontend to backend, backend to database, backend to Kafka).

## 2. Common Issues and Solutions

### 2.1. Backend Issues

#### Issue: `TypeError: AnalyticsService.get_prediction_outcome_summary() got an unexpected keyword argument 'time_since'`
- **Description:** This error occurs when the `get_prediction_outcome_summary` method in `AnalyticsService` is called with a `time_since` argument, but the method signature does not include it.
- **Solution:** Update the `get_prediction_outcome_summary` method in `backend/app/services/analytics_service.py` to accept `time_since: Optional[datetime] = None` and add filtering logic based on this parameter.

#### Issue: `AttributeError: 'FrameReader' object has no attribute 'isOpened'`
- **Description:** This error indicates that the `processing_worker.py` is trying to call `isOpened()` directly on a `FrameReader` object, but the method is only available on the internal `cv2.VideoCapture` object within `FrameReader`.
- **Solution:** Add an `isOpened` property to the `FrameReader` class in `backend/app/utils/video.py` that delegates to `self.cap.isOpened()`.

#### Issue: Backend server fails to start or crashes immediately.
- **Possible Causes:**
    - **Port in Use:** Another process is already using the required port (e.g., 8000).
    - **Missing Dependencies:** Python packages are not installed or are incompatible.
    - **Configuration Errors:** Incorrect values in `config.yaml` or environment variables.
    - **Database Connection Issues:** Database file not found or incorrect connection string.
- **Solutions:**
    - **Check Port:** Use `netstat -ano | findstr :8000` (Windows) or `lsof -i :8000` (Linux/macOS) to find the process using the port and terminate it.
    - **Reinstall Dependencies:** Run `pip install -r backend/requirements.txt`.
    - **Review Config:** Double-check `backend/configs/config.yaml` and your `.env` file.
    - **Database Path:** Ensure `backend/app.db` exists and is accessible.

#### Issue: ML models fail to load or perform inference.
- **Possible Causes:**
    - **Incorrect Model Path:** `model_path` in `config.yaml` is wrong.
    - **Missing ML Dependencies:** Libraries like `torch`, `ultralytics`, `onnxruntime` are not installed or have version conflicts.
    - **Hardware Issues:** Insufficient GPU memory or incorrect CUDA setup.
- **Solutions:**
    - **Verify Path:** Confirm the `model_path` in `backend/configs/config.yaml` points to the correct `.onnx` or `.pt` file.
    - **Install ML Dependencies:** Ensure all ML-related packages are installed as per `requirements.txt`.
    - **Check GPU/CUDA:** If using GPU, verify CUDA drivers and PyTorch/TensorFlow CUDA versions are compatible.

#### Issue: `ImportError: DLL load failed while importing onnxruntime_pybind11_state: A dynamic link library (DLL) initialization routine failed.`
- **Description:** This error typically occurs on Windows when ONNX Runtime cannot load its required DLLs. This can be due to missing Visual C++ Redistributables or an incompatible ONNX Runtime wheel.
- **Solutions:**
    - **Install Visual C++ Redistributables:** Ensure you have the latest Microsoft Visual C++ Redistributable for Visual Studio 2015, 2017, 2019, and 2022 installed. You can download it from the official Microsoft website.
    - **Reinstall ONNX Runtime:** Uninstall and reinstall `onnxruntime` to ensure a clean installation. It's crucial to install the correct wheel for your Python version and Windows architecture.
        ```bash
        pip uninstall onnxruntime onnxruntime-gpu
        pip install onnxruntime # or onnxruntime-gpu if you have a compatible GPU setup
        ```
    - **Check Python Environment:** Ensure your Python environment is not corrupted and is compatible with the ONNX Runtime version you are trying to install.

### 2.2. Frontend Issues

#### Issue: Frontend fails to compile or run.
- **Possible Causes:**
    - **Missing Node Modules:** `node_modules` directory is incomplete or corrupted.
    - **TypeScript Errors:** Type mismatches or syntax errors.
    - **Port in Use:** Another process is using the frontend port (e.g., 3000).
- **Solutions:**
    - **Reinstall Node Modules:** Run `npm install` in `frontend/`.
    - **Fix TypeScript Errors:** Run `npm run type-check` in `frontend/` and address reported issues.
    - **Check Port:** Change the port in `next.config.js` or terminate the conflicting process.

#### Issue: UI components are not styled correctly.
- **Possible Causes:**
    - **Tailwind CSS Configuration:** `tailwind.config.js` is incorrect or not properly configured to scan all relevant files.
    - **CSS Import Issues:** Tailwind CSS directives are not correctly imported in `globals.css`.
- **Solutions:**
    - **Review Tailwind Config:** Ensure `content` array in `tailwind.config.js` includes all paths where Tailwind classes are used.
    - **Check `globals.css`:** Verify that `@tailwind base;`, `@tailwind components;`, and `@tailwind utilities;` are present and correctly ordered.

### 2.3. Data Ingestion Issues

#### Issue: No data appearing in the system from Kafka.
- **Possible Causes:**
    - **Kafka Not Running:** Kafka and Zookeeper Docker containers are not up.
    - **Incorrect Topic Name:** Producer or consumer is using the wrong Kafka topic.
    - **Firewall:** Firewall blocking Kafka ports (9092).
    - **Producer/Consumer Not Running:** The Python scripts (`data_producer.py`, `data_consumer.py`) are not active.
- **Solutions:**
    - **Start Docker:** Run `docker-compose up -d` in the project root.
    - **Verify Topic:** Check `backend/data_ingestion/config.py` for correct topic names.
    - **Check Firewall:** Ensure ports are open.
    - **Run Scripts:** Manually start producer and consumer scripts if not automated.

## 3. Reporting Issues

When reporting an issue, please include:
- **Error Message:** The full traceback and error message.
- **Context:** What you were doing when the error occurred.
- **Steps to Reproduce:** Clear steps to consistently trigger the issue.
- **Environment:** Operating system, Python version, Node.js version, relevant package versions.
- **Logs:** Relevant log snippets from both frontend and backend.
