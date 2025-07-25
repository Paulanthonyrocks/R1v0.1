# Project Architecture: Traffic Management Hub

This document outlines the high-level architecture of the Traffic Management Hub, detailing its main components, their responsibilities, and how they interact.

## 1. Overview

The Traffic Management Hub is designed to provide real-time traffic monitoring, prediction, and anomaly detection capabilities. It consists of a modern web-based frontend, a robust backend API, and integrates with various data sources and machine learning models.

## 2. Core Components

### 2.1. Frontend (Next.js, TypeScript, Tailwind CSS)
- **Purpose:** User interface for displaying traffic data, predictions, alerts, and allowing user interaction.
- **Key Technologies:**
    - **Next.js:** React framework for server-side rendering and static site generation.
    - **TypeScript:** For type safety and improved developer experience.
    - **Tailwind CSS:** Utility-first CSS framework for rapid UI development.
    - **Zustand:** State management for the React application.
- **Structure:** Follows Next.js App Router conventions with `app/` for pages and `components/` for reusable UI elements.

### 2.2. Backend (FastAPI, Python)
- **Purpose:** Provides APIs for data ingestion, processing, analytics, machine learning inference, and real-time communication.
- **Key Technologies:**
    - **FastAPI:** Modern, fast (high-performance) web framework for building APIs with Python 3.7+ based on standard Python type hints.
    - **Python:** Primary language for backend logic, data processing, and machine learning.
- **Modules:**
    - **Data Ingestion:** Handles receiving and processing raw traffic data (e.g., from Kafka).
    - **ML/CV:** Integrates machine learning models (e.g., YOLOv8 for object detection, custom traffic predictors) for analysis.
    - **Analytics Service:** Provides functionalities for data aggregation, trend analysis, and prediction outcome summaries.
    - **WebSockets:** Manages real-time communication with the frontend for live updates.
    - **Authentication:** Handles user authentication and authorization (e.g., Firebase Admin SDK).

### 2.3. Database (SQLite, MongoDB)
- **Purpose:** Persistent storage for various types of data.
- **Technologies:**
    - **SQLite:** Used for structured data like prediction logs (`vehicle_data.db`).
    - **MongoDB (Optional/Planned):** For storing raw and processed traffic data, potentially for more flexible schema requirements.

### 2.4. Real-time Communication (WebSockets)
- **Purpose:** Enables live updates from the backend to the frontend for events like new predictions, alerts, and traffic congestion changes.
- **Implementation:** Managed by the `ConnectionManager` in the backend, broadcasting messages to subscribed clients.

### 2.5. Machine Learning & Computer Vision (PyTorch, Ultralytics, OpenCV, ONNX Runtime)
- **Purpose:** Core intelligence for traffic analysis, object detection, and prediction.
- **Technologies:**
    - **Ultralytics YOLOv8:** For real-time object detection (vehicles, pedestrians).
    - **PyTorch:** Deep learning framework.
    - **OpenCV:** For image and video processing.
    - **ONNX Runtime:** For efficient model inference.

## 3. Data Flow

1.  **Data Ingestion:** Raw traffic data (e.g., video streams, sensor data) is ingested into the system, potentially via Kafka.
2.  **Processing Worker:** Processes raw data, performs object detection (YOLOv8), and extracts relevant metrics.
3.  **Core Module:** Handles tracking of detected objects and integrates with the analytics service.
4.  **Analytics Service:** Stores processed data, generates predictions, detects anomalies, and provides summaries.
5.  **Database:** Stores prediction logs and other persistent data.
6.  **WebSockets:** Real-time updates are pushed to the frontend.
7.  **Frontend:** Displays processed data, predictions, and alerts to the user.

## 4. Future Considerations

-   Integration with external APIs (e.g., weather data, public transport schedules).
-   Scalability improvements for high-volume data ingestion and processing.
-   Advanced anomaly detection algorithms.
-   More sophisticated traffic simulation and control mechanisms.
