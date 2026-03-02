import { APIClient } from '../api/APIClient';

export interface HistoryStats {
    timestamp: string;
    vehicle_count: number;
    average_speed: number;
    congestion_score: number;
}

export const analyticsService = {
    getFeedHistory: async (feedId: string, hours: number = 24): Promise<HistoryStats[]> => {
        // Determine API URL based on environment or default to current origin
        const baseURL = process.env.NEXT_PUBLIC_API_BASE_URL || '/';
        const client = APIClient.getInstance({ baseURL });

        return client.get<HistoryStats[]>(`/api/v1/analytics/history/${feedId}`, { hours: hours.toString() }, { timeout: 60000 });
    }
};
