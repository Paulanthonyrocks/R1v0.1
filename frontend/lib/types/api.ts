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
