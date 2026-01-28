import { APIClient } from '../api/APIClient';

export interface HistoryStats {
    timestamp: string;
    vehicle_count: number;
    average_speed: number;
    congestion_score: number;
}

export const analyticsService = {
    getFeedHistory: async (feedId: string, hours: number = 24): Promise<HistoryStats[]> => {
        // Determine API URL based on environment or default
        const baseURL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
        const client = APIClient.getInstance({ baseURL });

        return client.get<HistoryStats[]>(`/api/v1/analytics/history/${feedId}`, { hours: hours.toString() });
    }
};
