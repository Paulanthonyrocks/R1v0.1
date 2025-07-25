import { User } from 'firebase/auth';

import { User } from 'firebase/auth';

export class TokenManager {
    private static instance: TokenManager;
    private currentToken: string | null = null;
    private tokenRefreshCallbacks: ((token: string) => void)[] = [];
    private currentUser: User | null = null; // Store the current user
    private refreshIntervalId: NodeJS.Timeout | null = null; // Store interval ID

    private constructor() {}

    static getInstance(): TokenManager {
        if (!TokenManager.instance) {
            TokenManager.instance = new TokenManager();
        }
        return TokenManager.instance;
    }

    async updateToken(user: User | null): Promise<void> {
        this.currentUser = user; // Update the current user

        // Clear any existing interval
        if (this.refreshIntervalId) {
            clearInterval(this.refreshIntervalId);
            this.refreshIntervalId = null;
        }

        if (user) {
            try {
                this.currentToken = await user.getIdToken(true);
                this.tokenRefreshCallbacks.forEach(callback => callback(this.currentToken!));

                // Start new interval for token expiry check
                this.refreshIntervalId = setInterval(async () => {
                    if (this.currentUser) { // Ensure user still exists
                        await this.checkTokenExpiry();
                    } else {
                        // If user logs out, stop the interval
                        this.stopMonitoring();
                    }
                }, 5 * 60 * 1000); // Check every 5 minutes
            } catch (error) {
                console.error('Error refreshing token:', error);
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
            await this.updateToken(this.currentUser);
        }
        return this.currentToken;
    }

    async checkTokenExpiry(): Promise<void> {
        if (!this.currentUser) {
            this.stopMonitoring();
            return;
        }
        try {
            const decodedToken = await this.currentUser.getIdTokenResult();
            const expirationTime = new Date(decodedToken.expirationTime).getTime();
            const now = Date.now();
            const fiveMinutes = 5 * 60 * 1000;

            if (expirationTime - now < fiveMinutes) {
                console.log('Token expiring soon, refreshing...');
                await this.refreshToken();
            }
        } catch (error) {
            console.error('Error checking token expiry:', error);
            // If there's an error checking expiry, it might mean the token is invalid
            // or user is no longer active. Stop monitoring.
            this.stopMonitoring();
        }
    }

    stopMonitoring(): void {
        if (this.refreshIntervalId) {
            clearInterval(this.refreshIntervalId);
            this.refreshIntervalId = null;
            this.currentUser = null;
            this.currentToken = null;
            console.log('Token monitoring stopped.');
        }
    }
}
