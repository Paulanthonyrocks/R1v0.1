import { APIClient } from '../api/APIClient';
import { getApiBrowserBaseURL } from '../api/backendBaseUrl';

// Wire contract (backend/app/routers/routing.py + app/models/routing.py):
// - POST /api/v1/routes/optimize  body RouteOptimizationRequest
//       {start_location: {latitude, longitude}, end_location: {latitude, longitude},
//        departure_time?: ISO datetime, preferences?: dict}
//       -> dict (geometry + metrics)
// - POST /api/v1/routes/personalized  body PersonalizedRouteRequest
//       {user_id, start_location, end_location, departure_time, route_preference?,
//        consider_weather, consider_events, max_alternatives}
//       -> PersonalizedRouteResponse
// - GET  /api/v1/routes/profile -> UserRoutingProfile (derived from the authed user)
// - GET  /api/v1/routes/supported-areas -> {supported_areas: SupportedArea[], last_updated}
export interface LatLon {
    latitude: number;
    longitude: number;
}

export type RoutePreference = 'highways' | 'scenic' | 'shortest' | 'least_traffic' | 'avoid_tolls';

export interface RouteOptimizationRequest {
    start_location: LatLon;
    end_location: LatLon;
    departure_time?: string;
    preferences?: Record<string, unknown>;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any -- backend returns a free-form dict (geometry + metrics)
export type OptimizedRoute = Record<string, any>;

export interface PersonalizedRouteRequest {
    user_id: string;
    start_location: LatLon;
    end_location: LatLon;
    departure_time?: string;
    route_preference?: RoutePreference;
    consider_weather: boolean;
    consider_events: boolean;
    max_alternatives: number;
}

export interface PersonalizedRouteResponse {
    primary_route: Record<string, unknown>;
    alternative_routes: Record<string, unknown>[];
    route_metadata: Record<string, unknown>;
    user_preferences_applied: string[];
    weather_impact?: Record<string, unknown> | null;
    event_impacts: Record<string, unknown>[];
    confidence_score: number;
}

export interface UserRoutingProfile {
    user_id: string;
    preferences: {
        user_id?: string;
        default_preference?: RoutePreference;
        avoid_road_types?: string[];
        preferred_road_types?: string[];
        max_tolls_amount?: number | null;
        preferred_departure_times?: Record<string, string>;
        avoid_intersections?: Record<string, number>[];
        favorite_routes?: string[];
        updated_at?: string;
    };
    time_patterns?: Record<string, unknown>;
    road_type_preferences?: Record<string, unknown>;
    common_destinations?: Record<string, unknown>[];
    routing_features?: Record<string, number>;
    last_updated?: string;
}

export interface SupportedArea {
    name: string;
    bounds: Record<string, number>;
    coverage_level: string;
}

export class RoutingService {
    private static instance: RoutingService;
    private apiClient: APIClient;

    private constructor() {
        this.apiClient = APIClient.getInstance({ baseURL: getApiBrowserBaseURL() });
    }

    public static getInstance(): RoutingService {
        if (!RoutingService.instance) {
            RoutingService.instance = new RoutingService();
        }
        return RoutingService.instance;
    }

    /** Distance/metrics-optimized route between two points. */
    async optimizeRoute(req: RouteOptimizationRequest): Promise<OptimizedRoute> {
        return this.apiClient.post<OptimizedRoute, RouteOptimizationRequest>('/api/v1/routes/optimize', req);
    }

    /** Preference-aware route with alternatives, weather and event impacts. */
    async personalizedRoute(req: PersonalizedRouteRequest): Promise<PersonalizedRouteResponse> {
        return this.apiClient.post<PersonalizedRouteResponse, PersonalizedRouteRequest>('/api/v1/routes/personalized', req);
    }

    /** The authed user's learned routing profile; null on transport failure. */
    async getProfile(): Promise<UserRoutingProfile | null> {
        try {
            return await this.apiClient.get<UserRoutingProfile>('/api/v1/routes/profile');
        } catch {
            return null;
        }
    }

    /** Coverage map: which regions the routing engine supports. */
    async getSupportedAreas(): Promise<SupportedArea[]> {
        try {
            const data = await this.apiClient.get<{ supported_areas: SupportedArea[]; last_updated?: string }>(
                '/api/v1/routes/supported-areas'
            );
            return data?.supported_areas ?? [];
        } catch {
            return [];
        }
    }
}

export const routingService = RoutingService.getInstance();
