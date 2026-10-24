import { APIClient } from '../api/APIClient';
import { getApiBrowserBaseURL } from '../api/backendBaseUrl';

// Wire contract (backend/app/routers/ws_monitoring.py):
// - GET /api/v1/websocket/health -> ConnectionStats (direct JSON, no APIResponse
//   wrapper; on internal error the backend returns an all-zero ConnectionStats
//   rather than a 5xx, so zeros mean "manager reported nothing", not "call failed").
// Statistics are a live snapshot of the WS connection manager: connections by
// user role, topic + per-feed subscription counts, per-client queue depths and
// round-trip latencies.

export interface ConnectionStats {
    total_connections: number;
    connections_by_role: Record<string, number>;
    total_subscriptions: number;
    feed_subscriptions: Record<string, number>;
    average_queue_size: number;
    max_queue_size: number;
    active_client_latencies: Record<string, number>;
}

export class WsMonitoringService {
    private static instance: WsMonitoringService;
    private apiClient: APIClient;

    private constructor() {
        this.apiClient = APIClient.getInstance({ baseURL: getApiBrowserBaseURL() });
    }

    public static getInstance(): WsMonitoringService {
        if (!WsMonitoringService.instance) {
            WsMonitoringService.instance = new WsMonitoringService();
        }
        return WsMonitoringService.instance;
    }

    /** Live WebSocket connection-manager statistics snapshot. */
    async getHealth(): Promise<ConnectionStats> {
        return this.apiClient.get<ConnectionStats>('/api/v1/websocket/health');
    }
}

export const wsMonitoringService = WsMonitoringService.getInstance();
