import { APIClient } from '../api/APIClient';
import { getBackendBaseURL } from '../api/backendBaseUrl';

// Wire contract (backend/app/routers/weather.py + backend/app/routers/events.py):
// - GET /api/v1/weather/impact?lat=&lon= -> backend-formatted weather impact card.
//   Severity is computed SERVER-side (weather_service._assess_weather_severity);
//   this replaces the panel's old client-side precip/wind thresholding.
// - GET /api/v1/events/impacts -> backend-formatted event impact cards
//   (event_service.get_event_impacts maps the external API's snake_case events
//   to this camelCase shape).
// Neither router wraps in APIResponse — responses are direct JSON.
// Failure modes surfaced as thrown errors: weather raises OperationFailed when
// the upstream weather API is unreachable/keyless; events raises 503/504/500
// when the external event API is unavailable.

export interface WeatherConditions {
    temperature: number;
    conditions: string;
    precipitation_chance: number;
    wind_speed: number;
}

/** Formatted weather impact (weather.py get_weather_impact). */
export interface WeatherImpact {
    type: 'weather';
    description: string;
    severity: string;
    location: string;
    startTime: string;
    details: WeatherConditions;
}

/** Raw external event payload (nested in EventImpact.details). */
export interface EnvironmentalEvent {
    type: string;
    description: string;
    severity: string;
    location: string;
    start_time: string;
    end_time?: string;
}

/** Formatted event impact (events.py get_event_impacts). */
export interface EventImpact {
    type: 'event';
    description: string;
    severity: string;
    location: string;
    startTime: string;
    endTime?: string;
    details: EnvironmentalEvent;
}

export class EnvironmentService {
    private static instance: EnvironmentService;
    private apiClient: APIClient;

    private constructor() {
        this.apiClient = APIClient.getInstance({ baseURL: getBackendBaseURL() });
    }

    public static getInstance(): EnvironmentService {
        if (!EnvironmentService.instance) {
            EnvironmentService.instance = new EnvironmentService();
        }
        return EnvironmentService.instance;
    }

    /** Weather impact assessment for a coordinate; severity scored server-side. */
    async getWeatherImpact(lat: number, lon: number): Promise<WeatherImpact> {
        return this.apiClient.get<WeatherImpact>('/api/v1/weather/impact', {
            lat: String(lat),
            lon: String(lon),
        });
    }

    /** Preformatted impact assessments of current routing-relevant events. */
    async getEventImpacts(): Promise<EventImpact[]> {
        return this.apiClient.get<EventImpact[]>('/api/v1/events/impacts');
    }
}

export const environmentService = EnvironmentService.getInstance();
