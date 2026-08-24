import { TokenManager } from '../auth/TokenManager';
import * as auth from 'firebase/auth';
import { errorNotifier } from '../utils/errorNotifier';
import { getBackendBaseURL, withTunnelPassword, getTunnelPassword } from './backendBaseUrl';

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
    // loca.lt tunnel password bypass is centralized in lib/api/backendBaseUrl.ts
    // (getTunnelPassword / withTunnelPassword) and shared with WebSocketClient.

    private constructor(options: APIOptions) {
        // Always connect to the backend directly. Base URL resolution
        // (env var, same-origin, dev default) is centralized in
        // lib/api/backendBaseUrl.ts.
        this.baseURL = options.baseURL || getBackendBaseURL();
        this.timeout = options.timeout || 30000;
        // `X-Tunnel-Password` header carries the tunnel secret for direct
        // (non-tunnel) backends so it never appears in the URL / access logs /
        // browser history. The query-param form (withTunnelPassword) is still
        // applied for the tunnel itself, which only inspects the URL it is
        // given. Sending both is harmless and lets deployments drop the
        // query param where the tunnel isn't in front.
        const headers: Record<string, string> = {
            'Content-Type': 'application/json',
            'ngrok-skip-browser-warning': '69420'
        };
        const tunnelPw = getTunnelPassword();
        if (tunnelPw) headers['X-Tunnel-Password'] = tunnelPw;
        this.headers = headers;
        this.tokenManager = TokenManager.getInstance();

        // Subscribe to token updates
        this.tokenManager.onTokenRefresh(this.handleTokenRefresh.bind(this));
    }

    /**
     * Idempotently append the loca.lt tunnel password as a query param so the
     * request bypasses the tunnel's 503 password gate. No-op when no password is
     * configured or the URL already carries one (e.g. on the 401-retry path
     * where response.url already has it).
     */
    static getInstance(options: APIOptions): APIClient {
        if (!APIClient.instance) {
            APIClient.instance = new APIClient(options);
            APIClient._options = options; // Store the options used for the first instance
        } else {
            // If an instance already exists, ensure the options are the same
            if (JSON.stringify(options) !== JSON.stringify(APIClient._options)) {
                console.log(`APIClient.getInstance called with different options. Updating instance...`);
                
                // Always use the provided baseURL from options
                APIClient.instance.baseURL = options.baseURL;
                console.log(`APIClient baseURL updated to: ${APIClient.instance.baseURL}`);
                
                // Update timeout if it changed
                if (options.timeout !== undefined && options.timeout !== APIClient._options.timeout) {
                    APIClient.instance.timeout = options.timeout;
                }
                
                APIClient._options = options;
            }
        }
        return APIClient.instance;
    }

    private handleTokenRefresh(token: string): void {
        // AUDIT FIX (2026-08-24): TokenManager signals logout with an empty token —
        // setting "Bearer " kept replaying a dead credential after sign-out. Clear instead.
        this.setAuthorizationHeader(token ? `Bearer ${token}` : null);
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

    async request<T>(path: string, options: RequestInit & { timeout?: number } = {}, retryAttempt: number = 0): Promise<T> {
        const url = withTunnelPassword(new URL(path, this.baseURL).toString());
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
            // AUDIT FIX (2026-08-24): without `await`, a rejected handleResponse
            // promise escapes this try/catch — 502/503/504 retries and error
            // notification were dead code for every non-OK response.
            return await this.handleResponse<T>(response, fetchOptions);
        } catch (error: unknown) {
            // Transient upstream/tunnel blips (e.g. loca.lt 503ing a single REST
            // call, or a gateway hiccup) should not hard-fail the caller. Retry a
            // bounded number of times with linear backoff before surfacing the
            // error. 401 refresh has its own retry path inside handleResponse and
            // is intentionally not retried here to avoid double-refresh loops.
            const status = (error as APIError)?.status;
            if ((status === 502 || status === 503 || status === 504) && retryAttempt < 2) {
                await new Promise((resolve) => setTimeout(resolve, 400 * (retryAttempt + 1)));
                return this.request<T>(path, options, retryAttempt + 1);
            }
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
