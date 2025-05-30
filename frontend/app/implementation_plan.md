# Implementation Plan: Pavement Analysis Frontend (Next.js)

This document outlines the implementation plan for the frontend of the Pavement Analysis system, integrating with the Python backend via API endpoints.

## Authentication Note (IMPORTANT)

Authentication is currently DISABLED for development and testing. This is temporary to facilitate faster development and testing. All endpoints in `/api/pavement`, `/api/stream`, and `/api/analysis` are publicly accessible. Before deploying to production, authentication MUST be re-enabled. Required steps:

1. Set up Firebase project and enable Authentication
2. Configure Firebase Admin SDK in backend:
   - Download service account key from Firebase Console
   - Place it at `backend/configs/firebase/service-account-key.json`
   - Update config.yaml with Firebase settings
3. Re-enable authentication in all router endpoints by:
   - Implementing `get_current_user` in `dependencies.py`
   - Adding `current_user = Depends(get_current_user)` to protected endpoints
4. Implement proper user roles and permissions
5. Add security headers and CORS configuration
6. Test complete authentication flow with Firebase client SDK

## Technology Stack

- **Framework:** Next.js (React)
- **Styling:** Tailwind CSS
- **Data Visualization:** Chart.js or Recharts for trend data (future)
- **API Communication:** Fetch API or Axios
- **State Management:** React Hooks (useState, useContext) for simpler cases, potentially Zustand or Jotai if complexity grows

## Project Structure

```text
pavement_frontend/
├── public/                 # Static assets
│   └── ...
├── src/
│   ├── api/                # API utility functions
│   │   └── index.js
│   ├── components/         # Reusable React components
│   │   ├── Layout.js
│   │   ├── FileUpload.js
│   │   ├── VideoPlayer.js
│   │   ├── ImageGallery.js
│   │   ├── AnalysisControls.js
│   │   ├── SummaryReport.js
│   │   └── LoadingSpinner.js
│   ├── pages/              # Next.js pages (routes)
│   │   ├── index.js        # Landing/Home page
│   │   └── analysis.js     # Analysis results page
│   ├── styles/             # Tailwind CSS configuration and global styles
│   │   └── globals.css
│   ├── lib/                # Utility functions (non-React)
│   │   └── ...
│   └── app/                # App Router directory (if using Next.js 13+/14 with App Router)
│       └── ...
├── tailwind.config.js
├── postcss.config.js
├── next.config.js
├── package.json
└── ...
```

## Key Frontend Components and Functionality

### 1. File Upload Component (`components/FileUpload.js`)

- **Purpose:** Allow users to select video or image files/directories for analysis
- **Features:**
  - Input field for file selection (`<input type="file">`)
  - Option to select single video or multiple images
  - Display selected file names
  - Button to initiate upload to the backend API
  - Visual feedback during upload (loading indicator)

### 2. Analysis Controls Component (`components/AnalysisControls.js`)

- **Purpose:** Provide input fields and controls for backend parameters
- **Features:**
  - Toggle switch for "Use Deep Learning" (`args.use_dl`)
  - Input field for "DL Model Path" (`args.model_path`)
  - Input field for "Frame Skip" (`args.frame_skip`)
  - Input field for "Calibration File Path" (`args.calib_file`)
  - Input field for "Segment Area (m²)" (`args.segment_area`)
  - Input field/Interactive UI for "Region of Interest (ROI)" (`args.roi_points`)
  - Button to trigger the analysis API call with selected parameters

### 3. Video Player / Image Gallery with Overlay

- **Purpose:** Display processed frames from the backend with visualization overlays
- **Features:**
  - **Video:**
    - Standard HTML5 video player (`<video>`)
    - Overlay a `<canvas>` element on top of the video
    - Draw bounding boxes, masks, labels, and PCI score on canvas
    - Pre-render overlays or fetch per frame as video plays
  - **Images:**
    - Display processed images in a gallery or slideshow format
    - Each image includes rendered overlays from backend
  - Navigation controls (play/pause, seek for video; next/previous for images)
  - Display current frame ID and timestamp

### 4. Summary Report Component (`components/SummaryReport.js`)

- **Purpose:** Display tabular data from backend report generator
- **Features:**
  - Data table component
  - Columns for essential metrics
  - Sorting and filtering capabilities (future)
  - Option to download raw CSV report

### 5. Loading Indicator (`components/LoadingSpinner.js`)

- **Purpose:** Provide visual feedback during long-running processes
- **Features:**
  - Spinner or progress bar component
  - Status text showing current process stage

### 6. Layout Component (`components/Layout.js`)

- **Purpose:** Define the basic page structure
- **Features:**
  - Consistent navigation header
  - Container for page content

### 7. API Utility Functions (`src/api/index.js`)

- **Purpose:** Centralize API calls to the backend
- **Functions:**
  - `uploadData(file, analysisParams)`: Sends data to `/analyze` endpoint
  - `getProcessedFrame(frameId)`: Fetches processed image frame
  - `getSummaryReport()`: Fetches summary report data
  - Endpoints for models/calibration files

### 8. Pages

- **`index.js` (Home/Upload Page):**
  - Contains upload and analysis control components
  - Handles file selection and parameter workflow
  - Navigates to analysis page on start
- **`analysis.js` (Results Page):**
  - Fetches and displays analysis results
  - Contains video/image and report components
  - Manages frame and report data state

## Backend API Endpoints (Required)

```typescript
// POST /upload_and_analyze
interface AnalysisRequest {
  file: File;
  parameters: AnalysisParameters;
}

// GET /analysis_status/:job_id
interface StatusResponse {
  status: 'processing' | 'complete' | 'error';
  progress: number;
  message: string;
}

// GET /processed_frames/:job_id/:frame_id
// Returns image binary

// GET /summary_report/:job_id
interface SummaryReport {
  metrics: Array<MetricData>;
  statistics: Statistics;
}
```

## Implementation Steps

1. Set up Next.js Project with Tailwind CSS
2. Create basic Flask/FastAPI application with placeholder endpoints
3. Create basic layout and navigation structure
4. Implement file upload component and API endpoint
5. Build analysis controls component
6. Implement API utility functions
7. Connect file upload and analysis workflow
8. Build video player and image gallery components
9. Implement summary report component
10. Add loading indicators and error handling
11. Refine UI/UX with proper styling
12. Connect to the actual backend implementation

## Challenges

- **Backend Integration:** Designing a robust API for file uploads and analysis
- **Video Frame Synchronization:** Syncing video playback with overlay data
- **Large Data Transfer:** Handling large video files and images efficiently
- **Real-time Feedback:** Providing granular updates on progress
- **Error Handling:** Clear communication of all errors

## Success Criteria

- Users can upload pavement data (video/images) via the UI
- Users can configure basic analysis parameters
- Analysis process triggers successfully via API
- Processed frames display with overlays
- Summary report displays in a readable format
- UI provides clear feedback during processing
