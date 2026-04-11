
import React, { createContext, useContext, useEffect, useMemo } from 'react';
import { WebSocketClient } from './WebSocketClient';
import { useAuth } from '../auth/AuthProvider';

const WebSocketContext = createContext<WebSocketClient | null>(null);

export const useWebSocket = () => {
    const context = useContext(WebSocketContext);
    if (!context) {
        throw new Error('useWebSocket must be used within a WebSocketProvider');
    }
    return context;
};

const getWsUrl = (path: string) => {
    let httpBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL;

    if (!httpBaseUrl && typeof window !== 'undefined') {
        httpBaseUrl = window.location.origin;
    }

    httpBaseUrl = httpBaseUrl || 'http://localhost:8000';

    const wsBaseUrl = httpBaseUrl.replace(/^(http)/, 'ws');
    
    return `${wsBaseUrl.replace(/\/$/, '')}${path}`;
}

const WS_BASE_URL = getWsUrl('/api/v1/ws');

export const WebSocketProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const { token, loading } = useAuth(); // Destructure loading state from useAuth

    const webSocketClient = useMemo(() => {
        return new WebSocketClient(WS_BASE_URL);
    }, []);

    useEffect(() => {
        webSocketClient.activate();
        console.log(`[WebSocketProvider] Mounted. Initial client instance: ${webSocketClient.getInstanceId()}`);
        // Cleanup on unmount - this is crucial
        return () => {
            console.log(`[WebSocketProvider] Unmounting. Destroying client instance: ${webSocketClient.getInstanceId()}`);
            webSocketClient.destroy();
        };
    }, [webSocketClient]);

    useEffect(() => {
        // Don't do anything while auth state is loading
        if (loading) {
            console.log("Auth state is loading, WebSocket connection deferred.");
            return;
        }

        // Use a small delay to debounce connection attempts
        // This prevents rapid connect/disconnect cycles during login/token refresh
        const debounceTimeout = setTimeout(() => {
            if (token) {
                // Only connect if there's a token and we are not already connected/connecting
                if (!webSocketClient.isConnected() && webSocketClient.getConnectionState() !== 'connecting') {
                    console.log(`[WebSocketProvider] Token available. Connecting instance: ${webSocketClient.getInstanceId()}`);
                    webSocketClient.connect(token).catch(error => {
                        console.error("WebSocket connection error on connect:", error);
                    });
                }
            } else {
                // Disconnect if there is no token (e.g., user logs out)
                if (webSocketClient.getConnectionState() !== 'disconnected') {
                    console.log(`[WebSocketProvider] No token. Disconnecting instance: ${webSocketClient.getInstanceId()}`);
                    webSocketClient.disconnect();
                }
            }
        }, 500); // 500ms debounce

        return () => clearTimeout(debounceTimeout);
    }, [token, loading, webSocketClient]); // Add loading to dependency array

    return (
        <WebSocketContext.Provider value={webSocketClient}>
            {children}
        </WebSocketContext.Provider>
    );
};
