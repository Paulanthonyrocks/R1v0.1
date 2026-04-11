// API Response wrapper type
export interface APIResponse<T> {
    status: 'success' | 'error';
    message?: string;
    data: T;
}

// Node Congestion Data Types
export interface NodeCongestionData {
    id: string;
    name: string;
    latitude: number;
    longitude: number;
    congestion_score: number;
    vehicle_count: number;
    average_speed: number;
    timestamp: string;
}

export interface AllNodesCongestionResponse {
    nodes: NodeCongestionData[];
}

// Real-time Metrics Types
export interface RealtimeMetrics {
    feed_id: string;
    timestamp: string;
    metrics: {
        vehicle_count?: number;
        average_speed?: number;
        congestion_level?: number;
        [key: string]: number | undefined;
    };
}

// Global Analytics Types
export interface GlobalMetrics {
    congestion_index: number;
    average_speed_kmh: number;
    active_incidents_count: number;
    total_flow: number;
    feed_statuses: {
        running: number;
        stopped: number;
        error: number;
    };
    timestamp: string;
}

// Vehicle & Tracking Types
export interface VehicleData {
    vehicle_id: string;
    global_vehicle_id?: string;
    bbox: [number, number, number, number]; // [x1, y1, x2, y2]
    class_id: number;
    class_name: string;
    confidence: number;
    // Enhanced Data
    vx?: number; // Velocity X (pixels/sec or relative)
    vy?: number; // Velocity Y
    gallery_size?: number; // ReID gallery size
    // Static/Optional fields (omitted in deltas)
    car_model?: string;
    car_model_confidence?: number;
    license_plate?: string;
    color?: string;
    behavior?: string;
    status?: string;
    is_occluded?: boolean;
    lane?: number;
}

export interface WebSocketVideoFrame {
    t: 'video_frame';
    f: string; // feed_id
    i: number; // frame_index
    ts: number; // timestamp
    v: VehicleData[];
    m: any; // metrics
    bg?: Uint8Array; // background (if adaptive)
    rois?: any[]; // regions of interest
}
