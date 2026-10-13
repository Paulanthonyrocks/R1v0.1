import { APIClient } from '../api/APIClient';
import { getBackendBaseURL } from '../api/backendBaseUrl';

// Wire contract (backend/app/routers/vehicles.py):
// - GET /api/v1/vehicles/                     -> List[IdentifiedVehicleResponse]
// - GET /api/v1/vehicles/{license_plate}      -> IdentifiedVehicleResponse (404 if unknown)
// - GET /api/v1/vehicles/tracks?feed_id=&license_plate=&class_id=&start_time=&end_time=&limit=&offset=
//                                             -> List[VehicleTrackResponse]
export interface IdentifiedVehicle {
    license_plate: string;
    vehicle_type?: string | null;
    make?: string | null;
    model?: string | null;
    color?: string | null;
    first_seen: number;
    last_seen: number;
    total_detections: number;
    flags?: string | null;
}

export interface VehicleTrack {
    feed_id: string;
    track_id: string;
    timestamp: number;
    class_id?: number | null;
    confidence?: number | null;
    bbox_x1?: number | null;
    bbox_y1?: number | null;
    bbox_x2?: number | null;
    bbox_y2?: number | null;
    speed?: number | null;
    license_plate?: string | null;
    lane?: number | null;
    direction?: string | null;
}

export class VehicleService {
    private static instance: VehicleService;
    private apiClient: APIClient;

    private constructor() {
        this.apiClient = APIClient.getInstance({ baseURL: getBackendBaseURL() });
    }

    public static getInstance(): VehicleService {
        if (!VehicleService.instance) {
            VehicleService.instance = new VehicleService();
        }
        return VehicleService.instance;
    }

    /** Identified vehicles (plate registry) with optional type/make/model filters. */
    async listIdentifiedVehicles(params: {
        limit?: number;
        offset?: number;
        vehicle_type?: string;
        make?: string;
        model?: string;
    } = {}): Promise<IdentifiedVehicle[]> {
        const query: Record<string, string> = {};
        if (params.limit !== undefined) query.limit = String(params.limit);
        if (params.offset !== undefined) query.offset = String(params.offset);
        if (params.vehicle_type) query.vehicle_type = params.vehicle_type;
        if (params.make) query.make = params.make;
        if (params.model) query.model = params.model;
        return this.apiClient.get<IdentifiedVehicle[]>('/api/v1/vehicles/', query);
    }

    /** Single identified vehicle by plate; null when the backend 404s. */
    async getVehicle(plate: string): Promise<IdentifiedVehicle | null> {
        try {
            return await this.apiClient.get<IdentifiedVehicle>(
                `/api/v1/vehicles/${encodeURIComponent(plate)}`
            );
        } catch {
            return null;
        }
    }

    /** Raw historical tracks, filterable per feed / plate / class / time range. */
    async listTracks(params: {
        limit?: number;
        offset?: number;
        feed_id?: string;
        license_plate?: string;
        class_id?: number;
        start_time?: number;
        end_time?: number;
    } = {}): Promise<VehicleTrack[]> {
        const query: Record<string, string> = {};
        if (params.limit !== undefined) query.limit = String(params.limit);
        if (params.offset !== undefined) query.offset = String(params.offset);
        if (params.feed_id) query.feed_id = params.feed_id;
        if (params.license_plate) query.license_plate = params.license_plate;
        if (params.class_id !== undefined) query.class_id = String(params.class_id);
        if (params.start_time !== undefined) query.start_time = String(params.start_time);
        if (params.end_time !== undefined) query.end_time = String(params.end_time);
        return this.apiClient.get<VehicleTrack[]>('/api/v1/vehicles/tracks', query);
    }
}

export const vehicleService = VehicleService.getInstance();
