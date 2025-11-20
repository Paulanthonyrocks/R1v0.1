import { useRef, useState, useCallback, useEffect } from 'react';
import { AlertData, FeedStatusData } from '@/lib/types';
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
    // nodeCongestionData: BackendCongestionNodeData[];
    isConnected: boolean;
    isReady: boolean;
    error: string | null;
    sendMessage: (action: string, payload?: object) => boolean;
    subscribeToFeed: (feedId: string) => void;
}

export const useRealtimeUpdates = (): RealtimeUpdates & { 
    feeds: FeedStatusData[],
    startFeed: (feedId: string) => void,
    stopFeed: (feedId: string) => void,
    startWebSocket: () => void
} => {
    const webSocketClientRef = useRef<WebSocketClient | null>(null);
    const [kpis, setKpis] = useState<KPIData | null>(null);
    const [alerts, setAlerts] = useState<AlertData[]>([]);
    const [feeds, setFeeds] = useState<FeedStatusData[]>([]);
    // const [nodeCongestionData, setNodeCongestionData] = useState<BackendCongestionNodeData[]>([]);
    const [isConnected, setIsConnected] = useState(false);
    const [isReady, setIsReady] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const hasRequestedInitialFeeds = useRef(false);

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

    const connect = useCallback(async () => {
        if (!webSocketClientRef.current) {
            const wsUrl = new URL('/api/v1/ws', process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000').toString();
            webSocketClientRef.current = new WebSocketClient(wsUrl);
            console.log('New WebSocket client created.');
        }
        const client = webSocketClientRef.current;

        if (client.isConnected() || client.getConnectionState() === 'connecting') {
            if (client.isConnected() && !hasRequestedInitialFeeds.current) {
                console.log("WebSocket already connected, requesting initial feed statuses.");
                sendMessage(WebSocketMessageType.GET_INITIAL_FEED_STATUSES, {});
                hasRequestedInitialFeeds.current = true;
            }
            return;
        }

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
    }, [sendMessage]);

    const startWebSocket = useCallback(() => {
        connect();
    }, [connect]);

    useEffect(() => {
        if (!webSocketClientRef.current) {
            const wsUrl = new URL('/api/v1/ws', process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000').toString();
            webSocketClientRef.current = new WebSocketClient(wsUrl);
            console.log('New WebSocket client created for subscriptions.');
        }
        const client = webSocketClientRef.current;
        const subscriptions: (() => void)[] = [];

        console.log('Setting up WebSocket subscriptions.');

        subscriptions.push(client.onStatusChange((status, message) => {
            console.log(`WebSocket status: ${status}`, message);
            const connected = status === 'connected';
            setIsConnected(connected);
            setIsReady(connected);

            if (connected && !hasRequestedInitialFeeds.current) {
                console.log("WebSocket connected and ready, requesting initial feed statuses.");
                sendMessage(WebSocketMessageType.GET_INITIAL_FEED_STATUSES, {});
                hasRequestedInitialFeeds.current = true;
            }
        }));

        subscriptions.push(client.onError((type, message) => {
            console.error(`WebSocket Error (${type}):`, message);
            setError(message);
        }));

        subscriptions.push(client.subscribe(WebSocketMessageType.INITIAL_FEED_STATUSES, (data: { feeds: FeedStatusData[] }) => {
            console.log("Received INITIAL_FEED_STATUSES data:", JSON.stringify(data));
            if (data && Array.isArray(data.feeds)) {
                setFeeds(data.feeds);
            } else {
                console.warn("Received INITIAL_FEED_STATUSES but data.feeds is not an array:", data);
            }
        }));

        subscriptions.push(client.subscribe(WebSocketMessageType.FEED_STATUS_UPDATE, (data: FeedStatusData) => {
            console.log("Received FEED_STATUS_UPDATE data:", JSON.stringify(data));
            setFeeds(prevFeeds => {
                const index = prevFeeds.findIndex(feed => feed.feed_id === data.feed_id);
                if (index !== -1) {
                    const newFeeds = [...prevFeeds];
                    newFeeds[index] = data;
                    return newFeeds;
                }
                return [...prevFeeds, data];
            });
        }));
        
        subscriptions.push(client.subscribe(WebSocketMessageType.KPI_UPDATE, (data: KPIData) => setKpis(data)));

        subscriptions.push(client.subscribe(WebSocketMessageType.NEW_ALERT, (data: { alert_data: AlertData }) => {
            if (data?.alert_data) {
                setAlerts(prevAlerts => [...prevAlerts, data.alert_data].slice(-10));
            }
        }));

        return () => {
            console.log('Cleaning up WebSocket subscriptions. In React Strict Mode, this runs on unmount.');
            subscriptions.forEach(unsubscribe => unsubscribe());
            hasRequestedInitialFeeds.current = false;
        };
    }, [sendMessage]);

    useEffect(() => {
        console.log("Internal feeds state updated:", JSON.stringify(feeds));
    }, [feeds]);

    const subscribeToFeed = useCallback((feedId: string) => {
        console.log(`Requesting to subscribe to feed: ${feedId}`);
        sendMessage(WebSocketMessageType.SUBSCRIBE_TO_FEED, { feed_id: feedId });
    }, [sendMessage]);

    const startFeed = useCallback((feedId: string) => {
        console.log(`Requesting to start feed: ${feedId}`);
        sendMessage(WebSocketMessageType.START_FEED, { feed_id: feedId });
    }, [sendMessage]);

    const stopFeed = useCallback((feedId: string) => {
        console.log(`Requesting to stop feed: ${feedId}`);
        sendMessage(WebSocketMessageType.STOP_FEED, { feed_id: feedId });
    }, [sendMessage]);

    return { 
        kpis, 
        alerts, 
        // nodeCongestionData, 
        isConnected, 
        isReady, 
        error, 
        feeds, 
        sendMessage,
        subscribeToFeed,
        startFeed,
        stopFeed,
        startWebSocket
    };
};