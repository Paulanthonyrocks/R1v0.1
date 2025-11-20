
import React, { createContext, useContext, useEffect, useMemo } from 'react';
import { WebSocketClient } from './WebSocketClient';
import useAuth from '../hook/useAuth';

const WebSocketContext = createContext<WebSocketClient | null>(null);

export const useWebSocket = () => {
    const context = useContext(WebSocketContext);
    if (!context) {
        throw new Error('useWebSocket must be used within a WebSocketProvider');
    }
    return context;
};

// Define the base URL for the WebSocket connection
const WS_BASE_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000/api/v1/ws';

export const WebSocketProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const { token } = useAuth();

    // Use useMemo to ensure the WebSocketClient is only created once
    const webSocketClient = useMemo(() => {
        console.log("Creating new WebSocketClient instance");
        return new WebSocketClient(WS_BASE_URL);
    }, []);

    useEffect(() => {
        if (token) {
            console.log("Token available, connecting WebSocket");
            webSocketClient.connect(token);
        } else {
            console.log("No token, disconnecting WebSocket");
            webSocketClient.disconnect();
        }

        // Cleanup on unmount
        return () => {
            console.log("WebSocketProvider unmounting, disconnecting WebSocket");
            webSocketClient.disconnect();
        };
    }, [token, webSocketClient]);

    return (
        <WebSocketContext.Provider value={webSocketClient}>
            {children}
        </WebSocketContext.Provider>
    );
};
