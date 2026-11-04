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

// --- Road / corridor registry (backend/app/routers/roads.py) ---
export interface RoadPoint {
    lat: number;
    lng: number;
}

export interface Road {
    id: string;
    name: string;
    polyline: RoadPoint[];
    speed_limit_kmh?: number | null;
    // Compass bearing (deg) of permitted travel when the road is one-way.
    one_way_bearing_deg?: number | null;
    intersection_ids?: string[];
    created_at: string;
    updated_at: string;
}

export interface RoadCreateInput {
    name: string;
    polyline: RoadPoint[];
    speed_limit_kmh?: number | null;
    one_way_bearing_deg?: number | null;
    intersection_ids?: string[];
}

export interface RoadUpdateInput {
    name?: string;
    polyline?: RoadPoint[];
    speed_limit_kmh?: number | null;
    one_way_bearing_deg?: number | null;
    intersection_ids?: string[];
}

// POST /api/v1/roads/snap result: nearest road to a map point.
export interface RoadSnap {
    found: boolean;
    road_id?: string | null;
    road_name?: string | null;
    point?: RoadPoint | null;
    distance_m?: number | null;
    // Arc length from the road's polyline start to the snapped point (m).
    distance_along_m?: number | null;
    // Local direction of travel of the snapped segment (deg).
    segment_bearing_deg?: number | null;
}

// GET /api/v1/roads/assets — signals and work-zones joined onto each road.
export interface RoadSignalAsset {
    signal_id: string;
    phase?: string | null;
    operational_status?: string | null;
    latitude: number;
    longitude: number;
    road_id: string;
    road_name?: string | null;
    chainage_m?: number | null;
    distance_m?: number | null;
    intersection_id?: string | null;
}

export interface RoadWorkZoneAsset {
    id?: string | null;
    feed_id?: string | null;
    lane?: number | string | null;
    speed_limit?: number | null;
    starts_at?: number | null;
    ends_at?: number | null;
    chainage_m?: number | null;
    road_name?: string | null;
}

export interface RoadAssets {
    road_name: string;
    signals: RoadSignalAsset[];
    work_zones: RoadWorkZoneAsset[];
}

export type RoadAssetsResponse = { roads: Record<string, RoadAssets> };

// GET/POST/PATCH/DELETE /api/v1/presets — warden presets (named camera groups).
export interface Preset {
    id: string;
    name: string;
    feed_ids: string[];
    road_id?: string | null;
    created_at: string;
    updated_at: string;
}

export type PresetCreateInput = { name: string; feed_ids: string[]; road_id?: string | null };
export type PresetUpdateInput = Partial<PresetCreateInput>;


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
    // Geo projection of the bbox bottom-center (present when the feed has
    // geo_calibration configured; backend homography, see geo_calibration.py)
    latitude?: number;
    longitude?: number;
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
