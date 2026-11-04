import { APIClient } from '../api/APIClient';
import { getApiBrowserBaseURL } from '../api/backendBaseUrl';
import { FeedStatusData } from '../types';
import { Preset, PresetCreateInput, PresetUpdateInput } from '../types/api';

// Wire contract (backend/app/routers/presets.py + app/models/presets.py):
// - GET    /api/v1/presets/                -> APIResponse{data: Preset[]}
// - POST   /api/v1/presets/                body PresetCreateInput -> APIResponse{data: Preset} (201)
// - GET    /api/v1/presets/{preset_id}     -> APIResponse{data: Preset}
// - GET    /api/v1/presets/{preset_id}/feeds -> APIResponse{data: FeedStatusData[]}
// - PATCH  /api/v1/presets/{preset_id}     body PresetUpdateInput -> APIResponse{data: Preset}
// - DELETE /api/v1/presets/{preset_id}     -> APIResponse{data: {deleted}}
// APIResponse-wrapped payloads read .data; the dev catch-all proxy forwards
// /api/v1/* to the backend unchanged.

class PresetService {
    private static instance: PresetService;
    private apiClient: APIClient;

    private constructor() {
        this.apiClient = APIClient.getInstance({ baseURL: getApiBrowserBaseURL() });
    }

    public static getInstance(): PresetService {
        if (!PresetService.instance) {
            PresetService.instance = new PresetService();
        }
        return PresetService.instance;
    }

    private unwrap<T>(payload: unknown): T {
        const p = payload as { data?: unknown };
        return (p && typeof p === 'object' && 'data' in p && p.data !== undefined ? p.data : payload) as T;
    }

    async listPresets(): Promise<Preset[]> {
        const raw = await this.apiClient.get<unknown>('/api/v1/presets/');
        return this.unwrap<Preset[]>(raw) ?? [];
    }

    async createPreset(input: PresetCreateInput): Promise<Preset> {
        const raw = await this.apiClient.post<unknown>('/api/v1/presets/', input);
        return this.unwrap<Preset>(raw);
    }

    async updatePreset(presetId: string, input: PresetUpdateInput): Promise<Preset> {
        const raw = await this.apiClient.patch<unknown>(`/api/v1/presets/${encodeURIComponent(presetId)}`, input);
        return this.unwrap<Preset>(raw);
    }

    async deletePreset(presetId: string): Promise<void> {
        await this.apiClient.delete(`/api/v1/presets/${encodeURIComponent(presetId)}`);
    }

    /** Live feed statuses for a preset's camera group. */
    async presetFeeds(presetId: string): Promise<FeedStatusData[]> {
        const raw = await this.apiClient.get<unknown>(`/api/v1/presets/${encodeURIComponent(presetId)}/feeds`);
        return this.unwrap<FeedStatusData[]>(raw) ?? [];
    }
}

export const presetService = PresetService.getInstance();
