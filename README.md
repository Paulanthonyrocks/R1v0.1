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
        # From the backend directory
        uvicorn app.main:app --reload --port 8000
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
