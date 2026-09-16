import { APIClient } from '../api/APIClient';
import { getBackendBaseURL } from '../api/backendBaseUrl';

export interface HistoryStats {
    timestamp: string;
    vehicle_count: number;
    average_speed: number;
    congestion_score: number;
}

export const analyticsService = {
    getFeedHistory: async (feedId: string, hours: number = 24): Promise<HistoryStats[]> => {
        // Determine API URL based on environment or default to current origin
        const baseURL = getBackendBaseURL();
        const client = APIClient.getInstance({ baseURL });

        // Analytics warms up slower than APIClient's ~1.2s retry window
        // (backend 503s history until ready). Retry 503s a few times here
        // before surfacing; anything else throws immediately.
        // eslint-disable-next-line no-await-in-loop -- intentional sequential retry with backoff
        for (let attempt = 0; attempt < 4; attempt += 1) {
            try {
                return await client.get<HistoryStats[]>(`/api/v1/analytics/history/${feedId}`, { hours: hours.toString() }, { timeout: 60000 });
            } catch (error: unknown) {
                const status = (error as { status?: number })?.status;
                if (status !== 503 || attempt === 3) throw error;
                await new Promise((resolve) => setTimeout(resolve, 2000 * (attempt + 1)));
            }
        }
        throw new Error('Unreachable: history retry loop fell through');
    }
};
