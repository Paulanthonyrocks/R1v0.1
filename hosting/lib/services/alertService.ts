import { APIClient } from '../api/APIClient';
import { getApiBrowserBaseURL } from '../api/backendBaseUrl';
import { errorNotifier } from '../utils/errorNotifier';

// Wire contract (backend/app/routers/alerts.py + app/models/alerts.py):
// - GET  /api/v1/alerts/?severity=&feed_id=&search=&page=&limit=
//        -> { alerts: BackendAlert[], total_count, page, limit }
// - PATCH /api/v1/alerts/{id}/acknowledge  body {acknowledged: bool} -> BackendAlert
// - DELETE /api/v1/alerts/{id} -> 204
// Alert ids are INTs (DB pk). WS new_alert payloads carry string ids, so this
// service keeps the numeric id as the wire-truth for REST paths.
export interface BackendAlert {
    id: number;
    timestamp: string;
    severity: 'INFO' | 'WARNING' | 'CRITICAL' | 'ERROR';
    feed_id?: string | null;
    message: string;
    latitude?: number | null;
    longitude?: number | null;
    details?: Record<string, unknown> | null;
    acknowledged: boolean;
    acknowledged_by?: string | null;
    acknowledged_at?: string | null;
    source_component?: string | null;
    tags?: string[] | null;
}

export interface AlertsPageResponse {
    alerts: BackendAlert[];
    total_count: number;
    page: number;
    limit: number;
}

export class AlertService {
    private static instance: AlertService;
    private apiClient: APIClient;

    private constructor() {
        this.apiClient = APIClient.getInstance({ baseURL: getApiBrowserBaseURL() });
    }

    public static getInstance(): AlertService {
        if (!AlertService.instance) {
            AlertService.instance = new AlertService();
        }
        return AlertService.instance;
    }

    /**
     * Fetches a page of alerts with optional filters.
     */
    async getAlerts(params: {
        severity?: string;
        feed_id?: string;
        search?: string;
        page?: number;
        limit?: number;
    } = {}): Promise<AlertsPageResponse> {
        const query: Record<string, string> = {};
        if (params.severity) query.severity = params.severity;
        if (params.feed_id) query.feed_id = params.feed_id;
        if (params.search) query.search = params.search;
        query.page = String(params.page ?? 1);
        query.limit = String(params.limit ?? 50);
        return this.apiClient.get<AlertsPageResponse>('/api/v1/alerts/', query);
    }

    /**
     * Acknowledges (or un-acknowledges) an alert. Admin-only on the backend;
     * failures surface via errorNotifier and return null.
     */
    async acknowledgeAlert(alertId: number, acknowledged: boolean = true): Promise<BackendAlert | null> {
        try {
            return await this.apiClient.patch<BackendAlert>(
                `/api/v1/alerts/${alertId}/acknowledge`,
                { acknowledged }
            );
        } catch (error) {
            errorNotifier.error(`Failed to acknowledge alert ${alertId}`);
            return null;
        }
    }

    /**
     * Deletes an alert. Admin-only on the backend; 204 on success.
     */
    async deleteAlert(alertId: number): Promise<boolean> {
        try {
            await this.apiClient.delete(`/api/v1/alerts/${alertId}`);
            return true;
        } catch (error) {
            errorNotifier.error(`Failed to delete alert ${alertId}`);
            return false;
        }
    }
}

export const alertService = AlertService.getInstance();
