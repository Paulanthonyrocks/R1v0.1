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
    private static readonly RETRYABLE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);
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
        } else if (JSON.stringify(options) !== JSON.stringify(APIClient._options)) {
            // If an instance already exists, ensure the options are the same.
            // Always use the provided baseURL from options.
            APIClient.instance.baseURL = options.baseURL;
            if (options.timeout !== undefined && options.timeout !== APIClient._options.timeout) {
                APIClient.instance.timeout = options.timeout;
            }
            APIClient._options = options;
        }
        return APIClient.instance;
    }

    private async fetchWithTimeout(url: string, options: RequestInit & { timeout?: number }): Promise<Response> {
        const controller = new AbortController();
        // AUDIT: compose the caller's abort signal with the deadline so a late
        // response body read is still cancellable; the deadline also clears only
        // after the body is consumed (see request/handleResponse).
        const signal = AbortSignal.any([controller.signal, ...(options.signal ? [options.signal] : [])]);
        const timeout = options.timeout || this.timeout;
        const timeoutId = setTimeout(() => controller.abort(), timeout);

        try {
            const response = await fetch(url, {
                ...options,
                signal
            });
            // AUDIT: attach the deadline cleanup to the response, so the timer is
            // cleared only after the body has been consumed (not before, which
            // previously let slow body reads race the cleared deadline).
            (response as Response & { __clearDeadline?: () => void }).__clearDeadline = () => clearTimeout(timeoutId);
            return response;
        } catch (error) {
            clearTimeout(timeoutId);
            const msg = error instanceof Error ? error.message : String(error);
            if (msg.includes("Failed to fetch")) {
                return new Response(JSON.stringify({ status: "unavailable", message: "Server unreachable" }), { status: 503, headers: { "Content-Type": "application/json" } });
            }
            throw error;
        }
    }

    private async handleResponse<T>(response: Response, originalRequestOptions: RequestInit & { timeout?: number }, authReplayed = false): Promise<T> {
        if (response.status === 204 || response.status === 205) {
            return undefined as T;
        }
        if (!response.ok) {
            if (response.status === 401 && !authReplayed) {
                // Token might be expired, try to refresh with firebase user
                // auth is already imported at the top of the file
                const user = auth.getAuth().currentUser;
                if (user) {
                    const newToken = await this.tokenManager.refreshToken();
                    if (newToken) {
                        // Retry the request with new token
                        const newHeaders = { ...originalRequestOptions.headers, 'Authorization': `Bearer ${newToken}` };
                        // AUDIT: a single refresh replay per request — a second 401 on the
                        // replayed request surfaces the error instead of refreshing forever.
                        return this.request<T>(response.url, {
                            ...originalRequestOptions,
                            headers: newHeaders,
                        }, 0, true);
                    }
                }
            }
            
            throw await this.buildApiError(response);
        }
        return response.json();
    }

    private async buildApiError(response: Response): Promise<APIError> {
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
        return error;
    }

    async request<T>(path: string, options: RequestInit & { timeout?: number } = {}, retryAttempt: number = 0, authReplayed = false): Promise<T> {
        const url = withTunnelPassword(new URL(path, this.baseURL).toString());
        // AUDIT (session/transport fix): Authorization is computed per request from
        // TokenManager instead of being cached on this.headers — a cached header went
        // stale after refresh/logout and replayed dead credentials.
        const token = this.tokenManager.getCurrentToken();
        const fetchOptions: RequestInit & { timeout?: number } = {
            ...options,
            headers: {
                ...this.headers,
                ...(token ? { Authorization: `Bearer ${token}` } : {}),
                ...options.headers
            }
        };

        try {
            const response = await this.fetchWithTimeout(url, fetchOptions);
            const clearDeadline = () => (response as Response & { __clearDeadline?: () => void }).__clearDeadline?.();
            // AUDIT FIX (2026-08-24): without `await`, a rejected handleResponse
            // promise escapes this try/catch — 502/503/504 retries and error
            // notification were dead code for every non-OK response.
            try {
                return await this.handleResponse<T>(response, fetchOptions, authReplayed);
            } finally {
                clearDeadline();
            }
        } catch (error: unknown) {
            // Transient upstream/tunnel blips (e.g. loca.lt 503ing a single REST
            // call, or a gateway hiccup) should not hard-fail the caller. Retry a
            // bounded number of times with linear backoff before surfacing the
            // error. 401 refresh has its own retry path inside handleResponse and
            // is intentionally not retried here to avoid double-refresh loops.
            const status = (error as APIError)?.status;
            const method = (options.method ?? 'GET').toUpperCase();
            // AUDIT: retry transient 5xx only for safe (idempotent) methods — a
            // retried POST/PUT/PATCH/DELETE could duplicate a mutation.
            if ((status === 502 || status === 503 || status === 504) && retryAttempt < 2
                && APIClient.RETRYABLE_METHODS.has(method)) {
                await new Promise((resolve) => setTimeout(resolve, 400 * (retryAttempt + 1)));
                return this.request<T>(path, options, retryAttempt + 1, authReplayed);
            }
            if (error instanceof Error && error.name === 'AbortError') {
                if (options.signal?.aborted) {
                    // AUDIT: caller-initiated cancellation surfaces as AbortError —
                    // it is not a timeout and must not mask the caller's signal.
                    throw error;
                }
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

    async patch<T, D = unknown>(path: string, data?: D): Promise<T> {
        return this.request<T>(path, {
            method: 'PATCH',
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

    /**
     * Authenticated binary fetch for sibling UI (images/clips).
     * Signature: getBlob(path: string, options?: RequestInit & { timeout?: number }): Promise<Blob>
     * Shares per-request Authorization, deadline+caller-abort composition and
     * tunnel-password handling with request(); errors are APIError like JSON paths.
     */
    async getBlob(path: string, options: RequestInit & { timeout?: number } = {}): Promise<Blob> {
        const url = withTunnelPassword(new URL(path, this.baseURL).toString());
        const token = this.tokenManager.getCurrentToken();
        const fetchOptions: RequestInit & { timeout?: number } = {
            ...options,
            headers: {
                ...this.headers,
                ...(token ? { Authorization: `Bearer ${token}` } : {}),
                ...options.headers
            }
        };
        try {
            const response = await this.fetchWithTimeout(url, fetchOptions);
            try {
                if (!response.ok) {
                    throw await this.buildApiError(response);
                }
                return await response.blob();
            } finally {
                (response as Response & { __clearDeadline?: () => void }).__clearDeadline?.();
            }
        } catch (error: unknown) {
            if (error instanceof Error && error.name === 'AbortError' && !options.signal?.aborted) {
                const errorMessage = 'Request timed out. Please check your internet connection or try again later.';
                errorNotifier.error(errorMessage);
                throw new Error(errorMessage);
            }
            throw error;
        }
    }
}
