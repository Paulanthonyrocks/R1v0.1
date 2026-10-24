import { APIClient } from '../api/APIClient';
import { getApiBrowserBaseURL } from '../api/backendBaseUrl';

// Wire contract (backend/app/routers/analysis.py + app/models/{analysis,traffic}.py):
// - GET  /api/v1/analytics/trends?region_id=&start_date=&end_date= -> AggregatedTrafficTrend
// - POST /api/v1/analytics/detect-anomalies  body {traffic_data_points: TrafficData[]} -> IncidentReport[]
// - POST /api/v1/analytics/predict-incident-likelihood  body {location, prediction_time?} -> dict
// - POST /api/v1/analytics/predictions/location  body {location, prediction_time?, prediction_window_hours?, include_historical_context?} -> PredictionResponse
// - GET  /api/v1/analytics/nodes/congestion -> APIResponse{data: {nodes: NodeCongestionData[]}}
// - GET  /api/v1/analytics/travel-times?hours= -> dict
// - GET  /api/v1/analytics/emissions?hours= -> dict
// - POST /api/v1/analytics/analyze/average_traffic  body {sensor_ids, start_time, end_time} -> APIResponse{data: {average_vehicle_count, average_speed}}
// - POST /api/v1/analytics/analyze/pattern  body {sensor_id, time_range} -> APIResponse{data: {average_vehicle_count, average_speed}}
// - POST /api/v1/analytics/analyze/anomaly  body {current_data, sensor_id, threshold} -> APIResponse{data: {is_anomaly}}
// - POST /api/v1/analytics/analyze/time_series  body {sensor_ids, start_time, end_time} -> APIResponse{data: {data}}
// - POST /api/v1/analytics/analyze/rolling_averages  body {time_series_data, window_size} -> APIResponse{data: {data}}
// APIResponse-wrapped payloads read .data; raw responses are direct.
export interface TrafficDataPoint {
    vehicle_count?: number;
    average_speed?: number;
    [key: string]: unknown;
}

export interface NodeCongestionData {
    id: string;
    name: string;
    latitude: number;
    longitude: number;
    congestion_score?: number | null;
    vehicle_count?: number | null;
    average_speed?: number | null;
    timestamp?: string;
}

export interface TrendSummary {
    region_id: string;
    start_time: string;
    end_time: string;
    average_congestion_score: number;
    contributing_sensors_count: number;
    total_vehicle_detections?: number | null;
    peak_hour?: string | null;
}

export interface IncidentLikelihoodPrediction {
    location?: { latitude: number; longitude: number };
    prediction_time?: string;
    incident_likelihood?: number;
    confidence_score?: number;
    contributing_factors?: string[];
    recommendations?: string[];
    historical_context?: Record<string, unknown> | null;
    [key: string]: unknown;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- travel-times/emissions return free-form dicts
export type FreeformReport = Record<string, any>;

export class ExploreService {
    private static instance: ExploreService;
    private apiClient: APIClient;

    private constructor() {
        this.apiClient = APIClient.getInstance({ baseURL: getApiBrowserBaseURL() });
    }

    public static getInstance(): ExploreService {
        if (!ExploreService.instance) {
            ExploreService.instance = new ExploreService();
        }
        return ExploreService.instance;
    }

    private unwrap<T>(payload: unknown): T {
        // APIResponse wrapper: {status, message, data}. Raw responses pass through.
        const p = payload as { data?: unknown };
        return (p && typeof p === 'object' && 'data' in p && p.data !== undefined ? p.data : payload) as T;
    }

    async getTrends(regionId: string, startDate: string, endDate: string): Promise<TrendSummary> {
        return this.apiClient.get<TrendSummary>('/api/v1/analytics/trends', {
            region_id: regionId, start_date: startDate, end_date: endDate,
        });
    }

    async detectAnomalies(points: TrafficDataPoint[]): Promise<unknown[]> {
        return this.apiClient.post<unknown[], { traffic_data_points: TrafficDataPoint[] }>(
            '/api/v1/analytics/detect-anomalies', { traffic_data_points: points }
        );
    }

    async predictIncidentLikelihood(lat: number, lon: number, predictionTime?: string): Promise<IncidentLikelihoodPrediction> {
        return this.apiClient.post<IncidentLikelihoodPrediction, unknown>(
            '/api/v1/analytics/predict-incident-likelihood',
            { location: { latitude: lat, longitude: lon }, prediction_time: predictionTime ?? null }
        );
    }

    async locationPredictions(lat: number, lon: number, windowHours = 24): Promise<IncidentLikelihoodPrediction> {
        return this.apiClient.post<IncidentLikelihoodPrediction, unknown>(
            '/api/v1/analytics/predictions/location',
            {
                location: { latitude: lat, longitude: lon },
                prediction_time: null,
                prediction_window_hours: windowHours,
                include_historical_context: true,
            }
        );
    }

    async nodesCongestion(): Promise<NodeCongestionData[]> {
        const raw = await this.apiClient.get<unknown>('/api/v1/analytics/nodes/congestion');
        const data = this.unwrap<{ nodes?: NodeCongestionData[] }>(raw);
        return data?.nodes ?? [];
    }

    async travelTimes(hours = 24): Promise<FreeformReport> {
        return this.apiClient.get<FreeformReport>('/api/v1/analytics/travel-times', { hours: String(hours) });
    }

    async emissions(hours = 24): Promise<FreeformReport> {
        return this.apiClient.get<FreeformReport>('/api/v1/analytics/emissions', { hours: String(hours) });
    }

    async analyzeAverageTraffic(sensorIds: string[], startTime: string, endTime: string): Promise<{ average_vehicle_count: number; average_speed: number }> {
        const raw = await this.apiClient.post<unknown, unknown>('/api/v1/analytics/analyze/average_traffic',
            { sensor_ids: sensorIds, start_time: startTime, end_time: endTime });
        return this.unwrap<{ average_vehicle_count: number; average_speed: number }>(raw);
    }

    async analyzePattern(sensorId: string, timeRange: string): Promise<{ average_vehicle_count: number; average_speed: number }> {
        const raw = await this.apiClient.post<unknown, unknown>('/api/v1/analytics/analyze/pattern',
            { sensor_id: sensorId, time_range: timeRange });
        return this.unwrap<{ average_vehicle_count: number; average_speed: number }>(raw);
    }

    async analyzeAnomaly(currentData: Record<string, number>, sensorId: string, threshold: number): Promise<{ is_anomaly: boolean }> {
        const raw = await this.apiClient.post<unknown, unknown>('/api/v1/analytics/analyze/anomaly',
            { current_data: currentData, sensor_id: sensorId, threshold });
        return this.unwrap<{ is_anomaly: boolean }>(raw);
    }

    async analyzeTimeSeries(sensorIds: string[], startTime: string, endTime: string): Promise<Record<string, unknown>[]> {
        const raw = await this.apiClient.post<unknown, unknown>('/api/v1/analytics/analyze/time_series',
            { sensor_ids: sensorIds, start_time: startTime, end_time: endTime });
        const data = this.unwrap<{ data?: Record<string, unknown>[] }>(raw);
        return data?.data ?? [];
    }

    async analyzeRollingAverages(timeSeriesData: Record<string, unknown>[], windowSize: string): Promise<Record<string, unknown>[]> {
        const raw = await this.apiClient.post<unknown, unknown>('/api/v1/analytics/analyze/rolling_averages',
            { time_series_data: timeSeriesData, window_size: windowSize });
        const data = this.unwrap<{ data?: Record<string, unknown>[] }>(raw);
        return data?.data ?? [];
    }
}

export const exploreService = ExploreService.getInstance();
