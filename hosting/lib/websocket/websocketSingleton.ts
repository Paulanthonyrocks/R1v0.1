import { WebSocketClient } from './WebSocketClient';

const SINGLETON_KEY = '__ws_client_singleton__';

function getGlobal(): any {
    if (typeof window !== 'undefined') return window;
    if (typeof globalThis !== 'undefined') return globalThis;
    return {};
}

/**
 * Gets the existing global WebSocketClient instance or creates a new one if it doesn't exist.
 * Stored on window/globalThis to ensure the connection survives React HMR/Fast Refresh.
 */
export function getOrCreateWebSocketClient(baseUrl: string): WebSocketClient {
    const global = getGlobal();
    if (!global[SINGLETON_KEY]) {
        global[SINGLETON_KEY] = new WebSocketClient(baseUrl);
        console.log(`[WebSocketSingleton] Created new global WebSocketClient instance: ${global[SINGLETON_KEY].getInstanceId()}`);
    }
    return global[SINGLETON_KEY];
}

/**
 * Returns the current global WebSocketClient instance, or null if it hasn't been created yet.
 */
export function getWebSocketClient(): WebSocketClient | null {
    const global = getGlobal();
    return global[SINGLETON_KEY] || null;
}

/**
 * Destroys the global WebSocketClient instance and clears the singleton.
 * Should be called during full application shutdown or explicit logout if required.
 */
export function destroyWebSocketClient(): void {
    const global = getGlobal();
    if (global[SINGLETON_KEY]) {
        console.log(`[WebSocketSingleton] Destroying global WebSocketClient instance: ${global[SINGLETON_KEY].getInstanceId()}`);
        global[SINGLETON_KEY].destroy();
        delete global[SINGLETON_KEY];
    }
}
