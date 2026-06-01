import { User, IdTokenResult } from 'firebase/auth';

export class TokenManager {
    private static instance: TokenManager;
    private currentToken: string | null = null;
    private tokenRefreshCallbacks: ((token: string) => void)[] = [];
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
        this.currentUser = user;

        if (this.refreshTimeoutId) {
            clearTimeout(this.refreshTimeoutId);
            this.refreshTimeoutId = null;
        }

        if (user) {
            try {
                const newToken = await user.getIdToken();
                if (newToken !== this.currentToken) {
                    this.currentToken = newToken;
                    this.tokenRefreshCallbacks.forEach(callback => callback(this.currentToken!));
                }
                this.scheduleTokenRefresh();
            } catch (error) {
                console.error('Error getting initial token:', error);
                this.currentToken = null;
            }
        } else {
            this.currentToken = null;
        }
    }

    getCurrentToken(): string | null {
        return this.currentToken;
    }

    onTokenRefresh(callback: (token: string) => void): () => void {
        this.tokenRefreshCallbacks.push(callback);
        return () => {
            this.tokenRefreshCallbacks = this.tokenRefreshCallbacks.filter(cb => cb !== callback);
        };
    }

    async refreshToken(): Promise<string | null> {
        if (this.currentUser) {
            try {
                console.log("Attempting to refresh token...");
                const newToken = await this.currentUser.getIdToken(true);
                this.currentToken = newToken;
                console.log("Token refreshed successfully.");
                this.tokenRefreshCallbacks.forEach(callback => callback(newToken));
                this.scheduleTokenRefresh();
                return newToken;
            } catch (error) {
                console.error("Error refreshing token:", error);
                this.stopMonitoring();
                return null;
            }
        }
        return this.currentToken;
    }

    private scheduleTokenRefresh() {
        if (this.refreshTimeoutId) {
            clearTimeout(this.refreshTimeoutId);
        }

        if (!this.currentUser) {
            return;
        }

        this.currentUser.getIdTokenResult()
            .then((idTokenResult: IdTokenResult) => {
                const expirationTime = new Date(idTokenResult.expirationTime).getTime();
                const now = Date.now();
                const refreshBuffer = 5 * 60 * 1000; // 5 minutes
                const refreshDelay = expirationTime - now - refreshBuffer;

                if (refreshDelay > 0) {
                    console.log(`Token refresh scheduled in ${Math.round(refreshDelay / 1000 / 60)} minutes.`);
                    this.refreshTimeoutId = setTimeout(async () => {
                        console.log('Proactively refreshing token as scheduled...');
                        await this.refreshToken();
                    }, refreshDelay);
                } else {
                    console.log('Token is close to expiry or expired, refreshing now...');
                    this.refreshToken();
                }
            })
            .catch((error: any) => {
                console.error('Error scheduling token refresh:', error);
                this.stopMonitoring();
            });
    }

    stopMonitoring(): void {
        if (this.refreshTimeoutId) {
            clearTimeout(this.refreshTimeoutId);
            this.refreshTimeoutId = null;
        }
        this.currentUser = null;
        this.currentToken = null;
        console.log('Token monitoring stopped.');
    }
}
