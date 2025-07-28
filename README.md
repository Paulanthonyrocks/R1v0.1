# Traffic Management Hub

## Project Overview

The Traffic Management Hub is a web application designed to provide a comprehensive view and control system for traffic management. It allows operators to monitor real-time traffic conditions, manage traffic signals, respond to incidents, and configure the system. The application aims to improve traffic flow, reduce congestion, and enhance road safety.

## Intended Audience

The primary users of the Traffic Management Hub are traffic management professionals, including:

*   **Traffic Operators:** Personnel responsible for monitoring and controlling traffic flow in real-time.
*   **Incident Responders:** Teams that need to react quickly and efficiently to traffic incidents.
*   **System Administrators:** Individuals who configure and maintain the traffic management system.
*   **City Planners:** Professionals who analyze traffic patterns to improve urban planning.

## Project Goals

*   Provide real-time traffic data visualization.
*   Enable remote management of traffic signals.
*   Facilitate quick responses to traffic incidents.
*   Offer system configuration capabilities.
*   Improve overall traffic efficiency and safety.
*   Provide a system that is built to scale.
*   Ensure security and reliability.

## Core Features

The Traffic Management Hub offers a range of features to address the needs of traffic management professionals:

*   **3D Globe Visualization:** The core of the application, providing a dynamic 3D view of the traffic environment.
*   **Real-time Traffic Data:** Display traffic flow, speed, and volume directly on the 3D globe.
*   **Signal Management:** Enables operators to remotely control and adjust traffic signal timings.
*   **Incident Management:** Allows operators to log and track incidents, manage responses, and view incidents on the map.
*   **System Configuration:** Provides system administrators with the ability to configure various settings, including traffic signal parameters, incident response protocols, and user access.
*   **Dashboard:** Presents an overview of system health, key metrics, and real-time alerts.
*   **User Management:** Enables system administrators to manage user accounts, access levels, and roles.
*   **Role-Based Access Control:** Provides control over the information that different user types can view and change.
*   **Alerts and Notifications:** Delivers real-time alerts and notifications to operators regarding critical incidents and system events.

## Technologies Used

*   **Frontend:**
    *   **Next.js:** React framework for building web applications.
    *   **React:** JavaScript library for building user interfaces.
    *   **TypeScript:** Typed superset of JavaScript for improved code quality.
    *   **Tailwind CSS:** Utility-first CSS framework for styling.
    *   **CesiumJS:** 3D mapping library for globe visualization.
*   **Backend:**
    *   **FastAPI:** High-performance Python web framework for building APIs.
    *   **Python:** Primary language for backend logic, data processing, and machine learning.
    *   **PyTorch, Ultralytics, OpenCV, ONNX Runtime:** For machine learning and computer vision tasks (e.g., object detection, traffic prediction).
*   **Database & Services:**
    *   **SQLite:** For structured data like prediction logs.
    *   **MongoDB (Optional):** For raw and processed traffic data.
    *   **WebSockets:** For real-time communication.
    *   **Firebase Admin SDK:** For authentication and user management.

## Setup Instructions

1.  **Prerequisites:**
    *   Node.js (version 16 or later)
    *   npm or yarn
    *   Python 3.9+
    *   `pip` (Python package installer)
    *   Firebase project set up (Authentication enabled, service account key for Admin SDK).

2.  **Installation:**

    ```bash
    # Clone the repository
    git clone <repository_url>
    cd traffic-management-hub

    # Install frontend dependencies
    cd frontend
    npm install
    cd ..

    # Install backend dependencies
    pip install -r backend/requirements.txt
    ```

3.  **Firebase Configuration:**

    *   Download your Firebase service account key JSON file from the Firebase Console (`Project settings > Service accounts > Generate new private key`).
    *   Place this file in `backend/configs/firebase/service-account-key.json`.
    *   Create a `.env` file in the project root and add your Firebase project ID:

        ```
        FIREBASE_PROJECT_ID=your_project_id
        ```

4.  **Running the Application:**

    *   **Start Backend:**

        ```bash
        uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000
        ```

    *   **Start Frontend:**

        ```bash
        cd frontend
        npm run dev
        ```

    Open your browser and go to `http://localhost:3000` to view the application.

## Development Plans

### Near-Term

*   **Data Ingestion:** Refine Kafka integration and data processing pipelines.
*   **ML Integration:** Fully integrate YOLOv8 for object detection and initial traffic prediction models.
*   **Analytics Service:** Enhance data aggregation, trend analysis, and prediction outcome summaries.
*   **Real-time Updates:** Improve WebSocket communication for various data streams.

### Mid-Term

*   **Advanced ML Models:** Explore and integrate more sophisticated traffic prediction and anomaly detection algorithms.
*   **UI Enhancements:** Develop interactive dashboards, alert management, and historical data visualization.
*   **User Management:** Implement comprehensive user roles and permissions.

### Long-Term

*   **Scalability:** Optimize for high-volume data and distributed deployments.
*   **External Integrations:** Connect with external data sources (e.g., weather APIs, public transport data).
*   **Simulation & Control:** Implement advanced traffic simulation and signal control mechanisms.

## Contributing

Contributions are welcome! Please follow these guidelines:

1.  Fork the repository.
2.  Create a new branch for your feature or bug fix.
3.  Make your changes and commit them with clear messages.
4.  Submit a pull request to the main branch.

## License

This project is licensed under the [MIT License](LICENSE).
