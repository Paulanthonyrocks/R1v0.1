# Traffic Management Hub (RLM-V0.1)

## Project Overview

The Traffic Management Hub is an advanced AI-powered surveillance and traffic analytics platform. It leverages real-time computer vision (YOLOv8) and distributed processing to provide operators with deep insights into traffic flow, vehicle classification, and anomaly detection.

## Core Features

*   **Surveillance Matrix:** A dynamic, multi-feed grid view with "Focus Mode" for high-priority monitoring.
*   **AI Video Analytics:** Real-time vehicle detection (YOLOv8), tracking (Centroid/ReID), and speed estimation.
*   **ROI-Based Detection:** Frontend-configurable Regions of Interest (ROI) for targeted lane monitoring and counting.
*   **Adaptive Streaming:** Intelligent backend that adjusts FPS and processing intensity based on client demand and system resources.
*   **Real-time KPI Dashboard:** Instant visualization of traffic volume, average speed, and anomaly alerts.
*   **Unified Dashboard Shell:** Consistent, theme-aware navigation (Dark Mode) across Surveillance, Analytics, Map, and Preferences.
*   **Cross-Camera Tracking:** Redis-backed Re-Identification (ReID) for tracking vehicles across different camera feeds.

## Technologies Used

*   **Frontend:**
    *   **Next.js 16 & React 19:** Modern, high-performance web framework.
    *   **TypeScript:** Full type safety across the application.
    *   **Tailwind CSS:** Responsive, utility-first styling with a custom "Matrix" theme.
    *   **Lucide React:** Consistent iconography.
*   **Backend:**
    *   **FastAPI:** High-performance asynchronous API layer.
    *   **Distributed Processing:** Decoupled architecture using `FeedManager`, `IngestionWorker`, and `InferenceWorker`.
    *   **Computer Vision:** YOLOv8 (Ultralytics), OpenCV, and ONNX Runtime for optimized inference.
    *   **Redis:** Fast state management for vehicle re-identification and tracking.
*   **Infrastructure:**
    *   **Firebase:** Authentication and hosting.
    *   **WebSockets:** Low-latency binary broadcasting for video frames and telemetry.

## Setup Instructions

1.  **Prerequisites:**
    *   Node.js 20+ (LTS)
    *   Python 3.10+
    *   Redis Server (running on default port 6379)
    *   Firebase project for Auth.

2.  **Installation:**

    ```bash
    # Clone the repository
    git clone <repository_url>
    cd traffic-management-hub

    # Install Web Dashboard dependencies
    cd hosting
    npm install
    cd ..

    # Install Backend dependencies
    cd backend
    pip install -r requirements.txt
    ```

3.  **Environment Configuration:**

    *   Create a `.env` file in the root directory.
    *   Add your Firebase configuration and any necessary API keys.
    *   Ensure Redis is accessible at `localhost:6379`.

4.  **Running the Application:**

    *   **Start Backend:**
        ```bash
        # REQUIRED when the backend is reachable through a tunnel (loca.lt,
        # cloudworkstations.dev, ...). The gate is FAIL-CLOSED: with no secret
        # every request except /health is rejected 401 and the WebSocket upgrade
        # is rejected 403 -- which the browser surfaces as a reconnect storm,
        # not as an auth error, so it looks like a tunnel problem.
        #
        # There is no .env loader in this repo, so the value must be in the
        # environment of the process that serves requests. Write it once:
        #   python -c "import secrets; print(secrets.token_urlsafe(32))" \
        #       > /kaggle/working/R1v0.1/backend/keys/tunnel_auth.token
        #   chmod 600 /kaggle/working/R1v0.1/backend/keys/tunnel_auth.token
        #   export TUNNEL_AUTH_TOKEN_FILE=/kaggle/working/R1v0.1/backend/keys/tunnel_auth.token
        # Only the PATH goes in the env var, so the secret survives a restart,
        # a cell re-run, or a fresh kernel without being re-pasted. The path
        # MUST be absolute: this command is valid from the repo root and from
        # backend/, and a $PWD-relative path silently breaks in one of them.
        #
        # The SAME value must reach the frontend as NEXT_PUBLIC_TUNNEL_AUTH_TOKEN
        # (hosting/.env.local). It is appended as `?tunnel_token=` on both REST
        # and WS -- the browser WS API cannot set headers on a handshake. See
        # backend/tunnel_token.env.example for the full checklist.
        #
        # To disable the gate explicitly (local dev, never a public box):
        #   export TUNNEL_AUTH_REQUIRED=0

        # From the backend directory
        # uvicorn app.main:app --port 8000
        # NOTE: if you start the backend with the CLI instead of `python -m app.main`,
        # pass the protocol-keepalive flags or tunnelled WebSockets die at ~40s:
        #   uvicorn app.main:app --port 8000 --ws-ping-interval null --ws-ping-timeout null --timeout-graceful-shutdown 25
        # (see app/main.py __main__ comment for the full root-cause)
        #
        # Do NOT pass --reload. The reloader watches the whole backend/ cwd,
        # which contains logs/ -- the directory the backend itself writes to --
        # so its own log output becomes filesystem events (~170/min, 76% of
        # backend_main.log in the Sep-30 run). A reload also kills 3 GPU
        # inference workers, every ingestion process and every live feed, then
        # brings them back cleanly, so a mid-session restart leaves no other
        # trace. app/main.py logs the real state at boot:
        #   autoreload: DISABLED (...)
        # and warns if --reload is in use. `python -m app.main` is unaffected
        # (reload=False there since 2026-10-01).
        ```

    *   **Start Frontend:**
        ```bash
        # From the hosting directory
        npm run dev
        ```

    Open `http://localhost:3000` to access the Dashboard.

## Architecture Highlights

*   **Broadcast Backpressure:** The `FeedManager` implements a specialized `broadcast_queue` to ensure slow network clients don't block the video processing pipeline.
*   **Parent-Monitoring Workers:** Sub-processes are hardened against "zombie" states by monitoring the parent PID, ensuring clean resource teardown on backend restarts.
*   **Binary WebSocket Protocol:** Video frames are broadcast as raw binary data to minimize serialization overhead and latency.

## License

This project is licensed under the MIT License.
