import { User, IdTokenResult } from 'firebase/auth';

export class TokenManager {
    private sessionGeneration = 0;
    private refreshInFlight: Promise<string | null> | null = null;
    private static instance: TokenManager;
    private currentToken: string | null = null;
    private tokenRefreshCallbacks: ((token: string | null) => void)[] = [];
    private currentUser: User | null = null;
    private refreshTimeoutId: NodeJS.Timeout | null = null;

    private constructor() {}

    static getInstance(): TokenManager {
        if (!TokenManager.instance) {
            TokenManager.instance = new TokenManager();
        }
        return TokenManager.instance;
    }

    async updateToken(user: User | null): Promise<void> {
        const generation = ++this.sessionGeneration;
        this.refreshInFlight = null;
        if (this.currentUser !== user && this.currentToken !== null) {
            this.currentToken = null;
            this.tokenRefreshCallbacks.forEach(callback => callback(null));
        }
        this.currentUser = user;

        if (this.refreshTimeoutId) {
            clearTimeout(this.refreshTimeoutId);
            this.refreshTimeoutId = null;
        }

        if (user) {
            try {
                const newToken = await user.getIdToken();
                if (this.sessionGeneration !== generation) return;
                if (newToken !== this.currentToken) {
                    this.currentToken = newToken;
                    this.tokenRefreshCallbacks.forEach(callback => callback(this.currentToken));
                }
                this.scheduleTokenRefresh(generation);
            } catch (error) {
                if (this.sessionGeneration !== generation) return;
                console.error('Error getting initial token:', error);
                this.stopMonitoring();
            }
        } else {
            this.currentToken = null;
            this.tokenRefreshCallbacks.forEach(callback => callback(null));
        }
    }

    getCurrentToken(): string | null {
        return this.currentToken;
    }

    onTokenRefresh(callback: (token: string | null) => void): () => void {
        this.tokenRefreshCallbacks.push(callback);
        return () => {
            this.tokenRefreshCallbacks = this.tokenRefreshCallbacks.filter(cb => cb !== callback);
        };
    }

    refreshToken(): Promise<string | null> {
        if (this.refreshInFlight) return this.refreshInFlight;
        if (!this.currentUser) return Promise.resolve(null);
        const generation = this.sessionGeneration;
        const refresh = this.performRefresh(generation).finally(() => {
            if (this.refreshInFlight === refresh) this.refreshInFlight = null;
        });
        this.refreshInFlight = refresh;
        return refresh;
    }

    private async performRefresh(generation: number): Promise<string | null> {
        if (this.currentUser) {
            try {
                console.log("Attempting to refresh token...");
                const newToken = await this.currentUser.getIdToken(true);
                if (this.sessionGeneration !== generation) return null;
                this.currentToken = newToken;
                console.log("Token refreshed successfully.");
                this.tokenRefreshCallbacks.forEach(callback => callback(newToken));
                this.scheduleTokenRefresh(generation);
                return newToken;
            } catch (error) {
                console.error("Error refreshing token:", error);
                if (this.sessionGeneration === generation) {
                    this.stopMonitoring();
                }
                return null;
            }
        }
        // AUDIT FIX (2026-08-24): with no signed-in user, returning the stale
        // currentToken made AUTH_FAILURE retry loops reconnect-with-the-same-bad-token
        // forever (5s spin). Return null so callers stop retrying.
        return null;
    }

    private scheduleTokenRefresh(generation: number) {
        if (this.sessionGeneration !== generation) return;
        if (this.refreshTimeoutId) {
            clearTimeout(this.refreshTimeoutId);
        }

        if (!this.currentUser) {
            return;
        }
        this.currentUser.getIdTokenResult()
            .then((idTokenResult: IdTokenResult) => {
                if (this.sessionGeneration !== generation) return;
                const expirationTime = new Date(idTokenResult.expirationTime).getTime();
                const now = Date.now();
                const refreshBuffer = 5 * 60 * 1000; // 5 minutes
                const refreshDelay = expirationTime - now - refreshBuffer;

                if (refreshDelay > 0) {
                    console.log(`Token refresh scheduled in ${Math.round(refreshDelay / 1000 / 60)} minutes.`);
                    this.refreshTimeoutId = setTimeout(() => {
                        console.log('Proactively refreshing token as scheduled...');
                        void this.refreshToken();
                    }, refreshDelay);
                } else {
                    console.log('Token is close to expiry or expired, refreshing now...');
                    this.refreshToken();
                }
            })
            .catch((error: any) => {
                console.error('Error scheduling token refresh:', error);
                if (this.sessionGeneration === generation) {
                    this.stopMonitoring();
                }
            });
    }

    stopMonitoring(): void {
        if (this.refreshTimeoutId) {
            clearTimeout(this.refreshTimeoutId);
            this.refreshTimeoutId = null;
        }
        this.currentUser = null;
        this.currentToken = null;
        ++this.sessionGeneration;
        this.refreshInFlight = null;
        this.tokenRefreshCallbacks.forEach(callback => callback(null));
        console.log('Token monitoring stopped.');
    }
}
