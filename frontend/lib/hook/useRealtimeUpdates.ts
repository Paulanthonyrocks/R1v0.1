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
    videoFrames: { [key: string]: ArrayBuffer };
    isConnected: boolean;
    isReady: boolean;
    error: string | null;
    sendMessage: (action: string, payload?: object) => boolean;
    subscribeToFeed: (feedId: string) => void;
}

export const useRealtimeUpdates = (): RealtimeUpdates & { feeds: FeedStatusData[] } => {
    const webSocketClientRef = useRef<WebSocketClient | null>(null);
    const [kpis, setKpis] = useState<KPIData | null>(null);
    const [alerts, setAlerts] = useState<AlertData[]>([]);
    const [feeds, setFeeds] = useState<FeedStatusData[]>([]);
    const [nodeCongestionData, setNodeCongestionData] = useState<BackendCongestionNodeData[]>([]);
    const [videoFrames, setVideoFrames] = useState<{ [key: string]: ArrayBuffer }>({});
    const [isConnected, setIsConnected] = useState(false);
    const [isReady, setIsReady] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const hasRequestedInitialFeeds = useRef(false);

    const WS_BASE_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000';

    const sendMessage = useCallback((action: string, payload?: object): boolean => {
        const client = webSocketClientRef.current;
        if (client && client.isConnected()) {
            try {
                client.send({ type: action as WebSocketMessageType, data: payload });
                return true;
            } catch (error) {
                console.error('Failed to send message:', error);
                return false;
            }
        }
        console.warn('WebSocket not connected. Message not sent:', { action, payload });
        return false;
    }, []);

    useEffect(() => {
        const wsUrl = new URL('/api/v1/video/ws', WS_BASE_URL).toString();
        const client = new WebSocketClient(wsUrl);
        webSocketClientRef.current = client;

        console.log('New WebSocket client created.');

        client.onStatusChange((status, message) => {
            console.log(`WebSocket status: ${status}`, message);
            const connected = status === 'connected';
            setIsConnected(connected);
            setIsReady(connected);

            // When connection is established, request initial data
            if (connected && !hasRequestedInitialFeeds.current) {
                console.log("WebSocket connected and ready, requesting initial feed statuses.");
                sendMessage(WebSocketMessageType.GET_INITIAL_FEED_STATUSES, {});
                hasRequestedInitialFeeds.current = true;
            }
        });

        client.onError((type, message) => {
            console.error(`WebSocket Error (${type}):`, message);
            setError(message);
        });

        // --- Subscription setup ---
        client.subscribe(WebSocketMessageType.INITIAL_FEED_STATUSES, (data: FeedStatusData[]) => {
            console.log("Received INITIAL_FEED_STATUSES data:", JSON.stringify(data));
            if (data && Array.isArray(data)) {
                setFeeds(data);
            } else {
                console.warn("Received INITIAL_FEED_STATUSES but data is not an array:", data);
            }
        });

        client.subscribe(WebSocketMessageType.VIDEO_FRAME, (data: { feed_id: string, frame: ArrayBuffer }) => {
            if (data && data.feed_id && data.frame) {
                setVideoFrames(prev => ({ ...prev, [data.feed_id]: data.frame }));
            }
        });
        
        client.subscribe(WebSocketMessageType.KPI_UPDATE, (data: KPIData) => setKpis(data));

        client.subscribe(WebSocketMessageType.NEW_ALERT, (data: { alert_data: AlertData }) => {
            if (data?.alert_data) setAlerts(prevAlerts => [...prevAlerts, data.alert_data]);
        });

        // --- End Subscription setup ---

        const connect = async () => {
            try {
                const currentToken = TokenManager.getInstance().getCurrentToken();
                if (currentToken) {
                    await client.connect(currentToken);
                } else {
                    console.warn('No auth token for WebSocket, will wait for token update.');
                }
            } catch (error) {
                setError(error instanceof Error ? error.message : 'Connection failed');
            }
        };

        connect();

        // Cleanup function
        return () => {
            console.log('Cleaning up WebSocket client.');
            client.destroy();
            webSocketClientRef.current = null;
            hasRequestedInitialFeeds.current = false; // Reset for the new client instance
            setIsConnected(false);
            setIsReady(false);
        };
    }, [WS_BASE_URL, sendMessage]); // sendMessage is stable due to useCallback

    useEffect(() => {
        console.log("Internal feeds state updated:", JSON.stringify(feeds));
    }, [feeds]);

    const subscribeToFeed = useCallback((feedId: string) => {
        console.log(`Requesting to subscribe to feed: ${feedId}`);
        sendMessage(WebSocketMessageType.SUBSCRIBE_TO_FEED, { feed_id: feedId });
    }, [sendMessage]);

    return { 
        kpis, 
        alerts, 
        nodeCongestionData, 
        isConnected, 
        isReady, 
        error, 
        feeds, 
        sendMessage,
        subscribeToFeed,
        videoFrames
    };
};