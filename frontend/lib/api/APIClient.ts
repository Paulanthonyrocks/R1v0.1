import { TokenManager } from '../auth/TokenManager';

export interface APIOptions {
    baseURL: string;
    timeout?: number;
}

export interface APIError extends Error {
    status?: number;
    statusText?: string;
    data?: unknown;
}

export class APIClient {
    private static instance: APIClient;
    private baseURL: string;
    private timeout: number;
    private headers: Record<string, string>;
    private tokenManager: TokenManager;

    private constructor(options: APIOptions) {
        this.baseURL = options.baseURL;
        this.timeout = options.timeout || 30000;
        this.headers = {
            'Content-Type': 'application/json'
        };
        this.tokenManager = TokenManager.getInstance();

        // Subscribe to token updates
        this.tokenManager.onTokenRefresh(this.handleTokenRefresh.bind(this));
    }

    static getInstance(options: APIOptions): APIClient {
        if (!APIClient.instance) {
            APIClient.instance = new APIClient(options);
        }
        return APIClient.instance;
    }

    private handleTokenRefresh(token: string): void {
        this.setAuthorizationHeader(`Bearer ${token}`);
    }

    public setAuthorizationHeader(value: string | null): void {
        if (value) {
            this.headers['Authorization'] = value;
        } else {
            delete this.headers['Authorization'];
        }
    }

    private async fetchWithTimeout(url: string, options: RequestInit): Promise<Response> {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), this.timeout);

        try {
            const response = await fetch(url, {
                ...options,
                signal: controller.signal
            });
            return response;
        } finally {
            clearTimeout(timeoutId);
        }
    }

    private async handleResponse<T>(response: Response): Promise<T> {
        if (!response.ok) {
            if (response.status === 401) {
                // Token might be expired, try to refresh with firebase user
                const auth = await import('firebase/auth');
                const user = auth.getAuth().currentUser;
                if (user) {
                    const newToken = await this.tokenManager.refreshToken(user);
                    if (newToken) {
                        // Retry the request with new token
                        return this.request<T>(response.url, {
                            method: response.type as string,
                            body: response.body
                        });
                    }
                }
            }
            
            const error = new Error(`API Error: ${response.status} ${response.statusText}`) as APIError;
            error.status = response.status;
            error.statusText = response.statusText;
            try {
                error.data = await response.json();
            } catch {
                // If response isn't JSON, use text
                error.data = await response.text();
            }
            throw error;
        }
        return response.json();
    }

    async request<T>(path: string, options: RequestInit = {}): Promise<T> {
        const url = new URL(path, this.baseURL).toString();
        const token = this.tokenManager.getCurrentToken();
        
        if (token) {
            this.headers['Authorization'] = `Bearer ${token}`;
        }

        const fetchOptions: RequestInit = {
            ...options,
            headers: {
                ...this.headers,
                ...options.headers
            }
        };

        try {
            const response = await this.fetchWithTimeout(url, fetchOptions);
            return this.handleResponse<T>(response);
        } catch (error: unknown) {
            if (error instanceof Error && error.name === 'AbortError') {
                throw new Error('Request timeout');
            }
            throw error;
        }
    }

    // Convenience methods
    async get<T>(path: string, params?: Record<string, string>): Promise<T> {
        const url = new URL(path, this.baseURL);
        if (params) {
            Object.entries(params).forEach(([key, value]) => {
                url.searchParams.append(key, value);
            });
        }
        return this.request<T>(url.toString(), { method: 'GET' });
    }

    async post<T, D = unknown>(path: string, data?: D): Promise<T> {
        return this.request<T>(path, {
            method: 'POST',
            body: data ? JSON.stringify(data) : undefined
        });
    }

    async put<T, D = unknown>(path: string, data?: D): Promise<T> {
        return this.request<T>(path, {
            method: 'PUT',
            body: data ? JSON.stringify(data) : undefined
        });
    }

    async delete<T>(path: string): Promise<T> {
        return this.request<T>(path, { method: 'DELETE' });
    }

    // Token status check
    async checkTokenStatus(): Promise<boolean> {
        try {
            await this.get('/api/v1/token/status');
            return true;
        } catch {
            // Ignore error details, just return false if status check fails
            return false;
        }
    }
}
