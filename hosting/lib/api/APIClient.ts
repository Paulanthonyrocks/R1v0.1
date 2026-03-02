import { TokenManager } from '../auth/TokenManager';
import * as auth from 'firebase/auth';
import { errorNotifier } from '../utils/errorNotifier';

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
    private static _options: APIOptions; // Store initial options
    private baseURL: string;
    private timeout: number;
    private headers: Record<string, string>;
    private tokenManager: TokenManager;

    private constructor(options: APIOptions) {
        // Ensure baseURL is an absolute URL
        let base = options.baseURL;
        if (!base || base === '/' || base.startsWith('/')) {
            if (typeof window !== 'undefined') {
                base = window.location.origin + (base === '/' ? '' : base);
            } else {
                base = 'http://localhost:8000'; // Fallback for SSR if needed
            }
        }
        this.baseURL = base.replace(/\/$/, ''); // Remove trailing slash
        
        this.timeout = options.timeout || 30000;
        this.headers = {
            'Content-Type': 'application/json',
            'Bypass-Tunnel-Reminder': 'true'
        };
        this.tokenManager = TokenManager.getInstance();

        // Subscribe to token updates
        this.tokenManager.onTokenRefresh(this.handleTokenRefresh.bind(this));
    }

    static getInstance(options: APIOptions): APIClient {
        if (!APIClient.instance) {
            APIClient.instance = new APIClient(options);
            APIClient._options = options; // Store the options used for the first instance
        } else {
            // If an instance already exists, ensure the options are the same
            if (JSON.stringify(options) !== JSON.stringify(APIClient._options)) {
                console.warn("APIClient.getInstance called with different options than initial instantiation. Ignoring new options.");
                // Optionally, you could throw an error here if strict singleton behavior is desired:
                // throw new Error("APIClient already instantiated with different options.");
            }
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

    private async fetchWithTimeout(url: string, options: RequestInit & { timeout?: number }): Promise<Response> {
        const controller = new AbortController();
        const timeout = options.timeout || this.timeout;
        const timeoutId = setTimeout(() => controller.abort(), timeout);

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

    private async handleResponse<T>(response: Response, originalRequestOptions: RequestInit & { timeout?: number }): Promise<T> {
        if (!response.ok) {
            if (response.status === 401) {
                // Token might be expired, try to refresh with firebase user
                // auth is already imported at the top of the file
                const user = auth.getAuth().currentUser;
                if (user) {
                    const newToken = await this.tokenManager.refreshToken();
                    if (newToken) {
                        // Retry the request with new token
                        const newHeaders = { ...originalRequestOptions.headers, 'Authorization': `Bearer ${newToken}` };
                        return this.request<T>(response.url, {
                            ...originalRequestOptions,
                            headers: newHeaders,
                        });
                    }
                }
            }
            
            const error = new Error(`API Error: ${response.status} ${response.statusText}`) as APIError;
            error.status = response.status;
            error.statusText = response.statusText;
            const text = await response.text();
            try {
                error.data = JSON.parse(text);
            } catch {
                // If response isn't JSON, use raw text
                error.data = text;
            }
            throw error;
        }
        return response.json();
    }

    async request<T>(path: string, options: RequestInit & { timeout?: number } = {}): Promise<T> {
        const url = new URL(path, this.baseURL).toString();
        const token = this.tokenManager.getCurrentToken();
        
        if (token) {
            this.headers['Authorization'] = `Bearer ${token}`;
        }

        const fetchOptions: RequestInit & { timeout?: number } = {
            ...options,
            headers: {
                ...this.headers,
                ...options.headers
            }
        };

        try {
            const response = await this.fetchWithTimeout(url, fetchOptions);
            return this.handleResponse<T>(response, fetchOptions);
        } catch (error: unknown) {
            if (error instanceof Error && error.name === 'AbortError') {
                const errorMessage = 'Request timed out. Please check your internet connection or try again later.';
                errorNotifier.error(errorMessage);
                throw new Error(errorMessage);
            }
            let errorMessage = 'An unknown API error occurred';
            if (error instanceof Error) {
                errorMessage = `An API error occurred: ${error.message}`;
            }

            errorNotifier.error(errorMessage);
            throw error;
        }
    }

    // Convenience methods
    async get<T>(path: string, params?: Record<string, string>, options: RequestInit & { timeout?: number } = {}): Promise<T> {
        const url = new URL(path, this.baseURL);
        if (params) {
            Object.entries(params).forEach(([key, value]) => {
                url.searchParams.append(key, value);
            });
        }
        return this.request<T>(url.toString(), { ...options, method: 'GET' });
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
