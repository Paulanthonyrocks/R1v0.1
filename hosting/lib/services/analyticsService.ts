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

        return client.get<HistoryStats[]>(`/api/v1/analytics/history/${feedId}`, { hours: hours.toString() }, { timeout: 60000 });
    }
};
