import { User } from 'firebase/auth';

export class TokenManager {
    private static instance: TokenManager;
    private currentToken: string | null = null;
    private tokenRefreshCallbacks: ((token: string) => void)[] = [];

    private constructor() {}

    static getInstance(): TokenManager {
        if (!TokenManager.instance) {
            TokenManager.instance = new TokenManager();
        }
        return TokenManager.instance;
    }

    async updateToken(user: User | null): Promise<void> {
        if (user) {
            try {
                this.currentToken = await user.getIdToken(true);
                // Notify all registered callbacks about the new token
                this.tokenRefreshCallbacks.forEach(callback => callback(this.currentToken!));
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

    // Register callback for token updates
    onTokenRefresh(callback: (token: string) => void): () => void {
        this.tokenRefreshCallbacks.push(callback);
        // Return unsubscribe function
        return () => {
            this.tokenRefreshCallbacks = this.tokenRefreshCallbacks.filter(cb => cb !== callback);
        };
    }

    // Force token refresh
    async refreshToken(user: User): Promise<string | null> {
        await this.updateToken(user);
        return this.currentToken;
    }

    // Check if token needs refresh
    async checkTokenExpiry(user: User): Promise<void> {
        const decodedToken = await user.getIdTokenResult();
        const expirationTime = new Date(decodedToken.expirationTime).getTime();
        const now = Date.now();
        const fiveMinutes = 5 * 60 * 1000;

        // If token expires in less than 5 minutes, refresh it
        if (expirationTime - now < fiveMinutes) {
            await this.refreshToken(user);
        }
    }
}
