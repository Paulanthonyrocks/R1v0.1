
import { VideoFrameMessage, VehicleFrontendData, SurveillanceFeedMessage } from '../types';

interface RegistryState {
    vehicles: Map<string, VehicleFrontendData>;
    lastSeen: Map<string, number>;
    lastUpdateTime: number;
}

class VideoStreamManager {
    private static instance: VideoStreamManager;
    private registries: Map<string, RegistryState> = new Map();
    private readonly STATE_UPDATE_INTERVAL = 200;

    private constructor() {}

    public static getInstance(): VideoStreamManager {
        if (!VideoStreamManager.instance) {
            VideoStreamManager.instance = new VideoStreamManager();
        }
        return VideoStreamManager.instance;
    }

    public updateVehicles(feedId: string, newVehicles: VehicleFrontendData[]) {
        const now = performance.now();
        if (!this.registries.has(feedId)) {
            this.registries.set(feedId, {
                vehicles: new Map(),
                lastSeen: new Map(),
                lastUpdateTime: 0
            });
        }

        const state = this.registries.get(feedId)!;

        // 1. Update registry
        newVehicles.forEach((v) => {
            const vid = v.vehicle_id;
            state.lastSeen.set(vid, now);

            const existing = state.vehicles.get(vid);
            if (existing) {
                const bboxChanged = JSON.stringify(existing.bbox) !== JSON.stringify(v.bbox);
                state.vehicles.set(vid, { 
                    ...existing, 
                    ...v, 
                    prev_bbox: bboxChanged ? existing.bbox : existing.prev_bbox,
                    last_update_time: bboxChanged ? now : existing.last_update_time
                });
            } else {
                state.vehicles.set(vid, { 
                    ...v, 
                    prev_bbox: v.bbox, 
                    last_update_time: now 
                } as VehicleFrontendData);
            }
        });

        // 2. Cleanup stale
        for (const [vid, lastSeen] of state.lastSeen.entries()) {
            if (now - lastSeen > 2000) {
                state.vehicles.delete(vid);
                state.lastSeen.delete(vid);
            }
        }

        state.lastUpdateTime = now;
    }

    public getVehicles(feedId: string): VehicleFrontendData[] {
        const state = this.registries.get(feedId);
        return state ? Array.from(state.vehicles.values()) : [];
    }

    public clearRegistry(feedId: string) {
        this.registries.delete(feedId);
    }

    public shouldUpdateUI(feedId: string): boolean {
        const state = this.registries.get(feedId);
        if (!state) return false;
        return (performance.now() - state.lastUpdateTime) > this.STATE_UPDATE_INTERVAL;
    }
}

export default VideoStreamManager.getInstance();
