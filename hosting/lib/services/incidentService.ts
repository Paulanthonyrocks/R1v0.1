import { APIClient } from '../api/APIClient';
import { getBackendBaseURL } from '../api/backendBaseUrl';
import { AlertData } from '../types';

export class IncidentService {
    private static instance: IncidentService;
    private apiClient: APIClient;

    private constructor() {
        this.apiClient = APIClient.getInstance({ baseURL: getBackendBaseURL() });
    }

    public static getInstance(): IncidentService {
        if (!IncidentService.instance) {
            IncidentService.instance = new IncidentService();
        }
        return IncidentService.instance;
    }

    /**
     * Acknowledges an incident.
     */
    async acknowledgeIncident(incidentId: string): Promise<boolean> {
        try {
            await this.apiClient.post(`/api/v1/incidents/${incidentId}/acknowledge`);
            return true;
        } catch (error) {
            console.error(`Failed to acknowledge incident ${incidentId}:`, error);
            return false;
        }
    }

    /**
     * Resolves an incident with optional notes.
     */
    async resolveIncident(incidentId: string, notes?: string): Promise<boolean> {
        try {
            await this.apiClient.post(`/api/v1/incidents/${incidentId}/resolve?notes=${encodeURIComponent(notes || '')}`);
            return true;
        } catch (error) {
            console.error(`Failed to resolve incident ${incidentId}:`, error);
            return false;
        }
    }

    /**
     * Marks an incident as a false positive.
     */
    async markFalsePositive(incidentId: string, notes?: string): Promise<boolean> {
        try {
            await this.apiClient.post(`/api/v1/incidents/${incidentId}/false-positive?notes=${encodeURIComponent(notes || '')}`);
            return true;
        } catch (error) {
            console.error(`Failed to mark incident ${incidentId} as false positive:`, error);
            return false;
        }
    }

    /**
     * Assigns an incident to a user.
     */
    async assignIncident(incidentId: string, userId: string): Promise<boolean> {
        try {
            await this.apiClient.post(`/api/v1/incidents/${incidentId}/assign`, {
                user_id: userId
            });
            return true;
        } catch (error) {
            console.error(`Failed to assign incident ${incidentId}:`, error);
            return false;
        }
    }

    /**
     * Creates a new incident.
     */
    async createIncident(data: {
        type: string;
        severity: string;
        description: string;
        latitude?: number;
        longitude?: number;
        feed_id?: string;
    }): Promise<AlertData | null> {
        try {
            return await this.apiClient.post('/api/v1/incidents/', data);
        } catch (error) {
            console.error('Failed to create incident:', error);
            return null;
        }
    }

    /**
     * Fetches incident statistics.
     */
    async getIncidentStats(): Promise<any> {
        try {
            return await this.apiClient.get('/api/v1/incidents/stats');
        } catch (error) {
            console.error('Failed to fetch incident stats:', error);
            return null;
        }
    }
}

export const incidentService = IncidentService.getInstance();
