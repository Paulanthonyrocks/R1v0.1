import { useEffect, useRef, useState, useCallback } from 'react';
import { AlertData, FeedStatusData, BackendCongestionNodeData } from '@/lib/types';
import { WebSocketClient, WebSocketMessageType } from '@/lib/websocket/WebSocketClient';
import { TokenManager } from '@/lib/auth/TokenManager';

interface VehicleData {
    x1: number;
    y1: number;
    x2: number;
    y2: number;
    id: string;
    speed: number;
}

interface KPIData {
    vehicles: VehicleData[]; 
    vehicle_count: number;
    avg_speed: number;
    [key: string]: unknown;
}

interface RealtimeUpdates {
    kpis: KPIData | null;
    alerts: AlertData[];
    nodeCongestionData: BackendCongestionNodeData[];
    isConnected: boolean;
    isReady: boolean;
    error: string | null;
    sendMessage: (action: string, payload?: object) => boolean;
    startWebSocket: () => void;
    reconnect: () => void;
}

export const useRealtimeUpdates = (): RealtimeUpdates & { feeds: FeedStatusData[] } => {
    const webSocketClientRef = useRef<WebSocketClient | null>(null);
    const [kpis, setKpis] = useState<KPIData | null>(null);
    const [alerts, setAlerts] = useState<AlertData[]>([]);
    const [feeds, setFeeds] = useState<FeedStatusData[]>([]);
    const [nodeCongestionData, setNodeCongestionData] = useState<BackendCongestionNodeData[]>([]);
    const [isConnected, setIsConnected] = useState(false);
    const [isReady, setIsReady] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const initializationRef = useRef(false);

    const WS_BASE_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000';

    // Initialize WebSocket client
    const initializeWebSocket = useCallback(() => {
        if (webSocketClientRef.current || initializationRef.current) {
            return; // Already initialized or in process
        }
        
        initializationRef.current = true;
        
        const wsUrl = new URL('/api/v1/ws', WS_BASE_URL).toString();
        const client = new WebSocketClient(wsUrl);

        // Setup error handling
        client.onError((type, message) => {
            console.error(`WebSocket Error (${type}):`, message);
            setError(message);
            
            // Don't set isConnected/isReady to false here as the client
            // will handle reconnection automatically
            if (type === 'max_reconnect_attempts' || type === 'auth_error') {
                setIsConnected(false);
                setIsReady(false);
            }
        });

        // Setup status change handling
        client.onStatusChange((status, message) => {
            console.log(`WebSocket status: ${status}`, message);
            
            const connected = status === 'connected';
            setIsConnected(connected);
            setIsReady(connected);
            
            if (connected) {
                setError(null);
            } else if (status === 'error') {
                setError(message || 'Connection error');
            }
        });

        // Subscribe to feed status updates
        client.subscribe(WebSocketMessageType.FEED_STATUS_UPDATE, 
            (data: { feed_status_data: FeedStatusData }) => {
                const updatedFeed = data.feed_status_data;
                setFeeds(prevFeeds => {
                    const feedIndex = prevFeeds.findIndex(f => f.id === updatedFeed.id);
                    if (feedIndex > -1) {
                        const newFeeds = [...prevFeeds];
                        newFeeds[feedIndex] = updatedFeed;
                        return newFeeds;
                    }
                    return [...prevFeeds, updatedFeed];
                });
            }
        );

        // Subscribe to initial feed statuses
        client.subscribe(WebSocketMessageType.INITIAL_FEED_STATUSES, 
            (data: { feeds: FeedStatusData[] }) => {
                if (data?.feeds) {
                    setFeeds(data.feeds);
                }
            }
        );

        // Subscribe to global realtime metrics
        client.subscribe(WebSocketMessageType.GLOBAL_REALTIME_METRICS_UPDATE, 
            (data: KPIData) => {
                setKpis(data);
            }
        );

        // Subscribe to new alerts
        client.subscribe(WebSocketMessageType.NEW_ALERT, 
            (data: { alert_data: AlertData }) => {
                if (data?.alert_data) {
                    setAlerts(prevAlerts => [...prevAlerts, data.alert_data]);
                }
            }
        );

        // Subscribe to node congestion updates
        client.subscribe(WebSocketMessageType.NODE_CONGESTION_UPDATE, 
            (data: { nodes: BackendCongestionNodeData[] }) => {
                if (data?.nodes) {
                    setNodeCongestionData(data.nodes);
                }
            }
        );

        // Subscribe to general notifications
        client.subscribe(WebSocketMessageType.GENERAL_NOTIFICATION, 
            (data: { message_type: string }) => {
                if (data?.message_type === 'auth_success') {
                    console.log('Authentication successful');
                    setError(null);
                }
            }
        );

        webSocketClientRef.current = client;
        
        return client;
    }, [WS_BASE_URL]);

    // Connect to WebSocket
    const connectWebSocket = useCallback(async () => {
        const client = webSocketClientRef.current;
        if (!client) {
            console.error('WebSocket client not initialized');
            return;
        }

        try {
            const currentToken = TokenManager.getInstance().getCurrentToken();
            if (currentToken) {
                await client.connect(currentToken);
            } else {
                console.warn('No authentication token available for WebSocket connection');
                setError('No authentication token available');
            }
        } catch (error) {
            console.error('Failed to connect WebSocket:', error);
            setError(error instanceof Error ? error.message : 'Connection failed');
        }
    }, []);

    // Initialize on mount
    useEffect(() => {
        if (!initializationRef.current) {
            initializeWebSocket();
        }
    }, [initializeWebSocket]);

    // Connect after initialization
    useEffect(() => {
        if (webSocketClientRef.current && !webSocketClientRef.current.isConnected()) {
            connectWebSocket();
        }
    }, [connectWebSocket]);

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            if (webSocketClientRef.current) {
                console.log('Cleaning up WebSocket connection');
                webSocketClientRef.current.destroy();
                webSocketClientRef.current = null;
            }
            initializationRef.current = false;
        };
    }, []);

    // Send message function
    const sendMessage = useCallback((action: string, payload?: object): boolean => {
        const client = webSocketClientRef.current;
        if (client && client.isConnected()) {
            try {
                client.send({ 
                    type: action as WebSocketMessageType, 
                    data: payload 
                });
                return true;
            } catch (error) {
                console.error('Failed to send message:', error);
                return false;
            }
        }
        
        console.warn('WebSocket not connected. Message not sent:', { action, payload });
        return false;
    }, []);

    // Manual reconnect function
    const reconnect = useCallback(async () => {
        const client = webSocketClientRef.current;
        if (client) {
            try {
                const currentToken = TokenManager.getInstance().getCurrentToken();
                if (currentToken) {
                    await client.reconnectWithNewToken(currentToken);
                } else {
                    setError('No authentication token available for reconnection');
                }
            } catch (error) {
                console.error('Manual reconnection failed:', error);
                setError(error instanceof Error ? error.message : 'Reconnection failed');
            }
        }
    }, []);

    // Deprecated function for backward compatibility
    const startWebSocket = useCallback(() => {
        console.warn("startWebSocket is deprecated. Connection is managed automatically.");
        reconnect();
    }, [reconnect]);

    return { 
        kpis, 
        alerts, 
        nodeCongestionData, 
        isConnected, 
        isReady, 
        error, 
        startWebSocket, 
        feeds, 
        sendMessage,
        reconnect
    };
};