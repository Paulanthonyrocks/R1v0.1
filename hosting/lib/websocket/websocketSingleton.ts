import { WebSocketClient } from './WebSocketClient';

let globalClient: WebSocketClient | null = null;

/**
 * Gets the existing global WebSocketClient instance or creates a new one if it doesn't exist.
 * This ensures that the WebSocket connection survives React HMR/Fast Refresh.
 */
export function getOrCreateWebSocketClient(baseUrl: string): WebSocketClient {
    if (!globalClient) {
        globalClient = new WebSocketClient(baseUrl);
        console.log(`[WebSocketSingleton] Created new global WebSocketClient instance: ${globalClient.getInstanceId()}`);
    }
    return globalClient;
}

/**
 * Returns the current global WebSocketClient instance, or null if it hasn't been created yet.
 */
export function getWebSocketClient(): WebSocketClient | null {
    return globalClient;
}

/**
 * Destroys the global WebSocketClient instance and clears the singleton.
 * Should be called during full application shutdown or explicit logout if required.
 */
export function destroyWebSocketClient(): void {
    if (globalClient) {
        console.log(`[WebSocketSingleton] Destroying global WebSocketClient instance: ${globalClient.getInstanceId()}`);
        globalClient.destroy();
        globalClient = null;
    }
}
