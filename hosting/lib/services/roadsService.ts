import { APIClient } from '../api/APIClient';
import { getApiBrowserBaseURL } from '../api/backendBaseUrl';
import { FeedStatusData } from '../types';
import { Road, RoadCreateInput, RoadAssetsResponse, RoadSnap, RoadUpdateInput } from '../types/api';

// Wire contract (backend/app/routers/roads.py + app/models/roads.py):
// - GET    /api/v1/roads/               -> APIResponse{data: Road[]}
// - POST   /api/v1/roads/               body RoadCreateInput -> APIResponse{data: Road} (201)
// - POST   /api/v1/roads/snap           body {latitude, longitude, max_distance_m?} -> APIResponse{data: RoadSnap}
// - GET    /api/v1/roads/{road_id}      -> APIResponse{data: Road}
// - GET    /api/v1/roads/{road_id}/feeds-> APIResponse{data: FeedStatusData[]}
// - PATCH  /api/v1/roads/{road_id}      body RoadUpdateInput -> APIResponse{data: Road}
// - DELETE /api/v1/roads/{road_id}      -> APIResponse{data: {deleted}}
// APIResponse-wrapped payloads read .data. The Next dev server proxies
// /api/v1/* via app/api/v1/[[...path]]/route.ts, so these hit the backend as-is.

class RoadsService {
    private static instance: RoadsService;
    private apiClient: APIClient;

    private constructor() {
        this.apiClient = APIClient.getInstance({ baseURL: getApiBrowserBaseURL() });
    }

    public static getInstance(): RoadsService {
        if (!RoadsService.instance) {
            RoadsService.instance = new RoadsService();
        }
        return RoadsService.instance;
    }

    private unwrap<T>(payload: unknown): T {
        // APIResponse wrapper: {status, message, data}. Raw responses pass through.
        const p = payload as { data?: unknown };
        return (p && typeof p === 'object' && 'data' in p && p.data !== undefined ? p.data : payload) as T;
    }

    async listRoads(): Promise<Road[]> {
        const raw = await this.apiClient.get<unknown>('/api/v1/roads/');
        return this.unwrap<Road[]>(raw) ?? [];
    }

    async getRoad(roadId: string): Promise<Road> {
        const raw = await this.apiClient.get<unknown>(`/api/v1/roads/${encodeURIComponent(roadId)}`);
        return this.unwrap<Road>(raw);
    }

    async createRoad(input: RoadCreateInput): Promise<Road> {
        const raw = await this.apiClient.post<unknown, RoadCreateInput>('/api/v1/roads/', input);
        return this.unwrap<Road>(raw);
    }

    async updateRoad(roadId: string, input: RoadUpdateInput): Promise<Road> {
        const raw = await this.apiClient.patch<unknown, RoadUpdateInput>(
            `/api/v1/roads/${encodeURIComponent(roadId)}`, input);
        return this.unwrap<Road>(raw);
    }

    async deleteRoad(roadId: string): Promise<void> {
        await this.apiClient.delete<unknown>(`/api/v1/roads/${encodeURIComponent(roadId)}`);
    }

    /** Snap a map point (e.g. a camera position) to the nearest registered road. */
    async snap(latitude: number, longitude: number, maxDistanceM = 150): Promise<RoadSnap> {
        const raw = await this.apiClient.post<unknown, { latitude: number; longitude: number; max_distance_m: number }>(
            '/api/v1/roads/snap',
            { latitude, longitude, max_distance_m: maxDistanceM },
        );
        return this.unwrap<RoadSnap>(raw);
    }

    /** Feeds whose config.road_id matches this road. */
    async feedsOnRoad(roadId: string): Promise<FeedStatusData[]> {
        const raw = await this.apiClient.get<unknown>(`/api/v1/roads/${encodeURIComponent(roadId)}/feeds`);
        return this.unwrap<FeedStatusData[]>(raw) ?? [];
    }

    /** Signals + active work-zones joined onto every road (GET /api/v1/roads/assets). */
    async getAssets(): Promise<RoadAssetsResponse> {
        const raw = await this.apiClient.get<unknown>('/api/v1/roads/assets');
        return this.unwrap<RoadAssetsResponse>(raw) ?? { roads: {} };
    }
}

export const roadsService = RoadsService.getInstance();
