import { useRef, useState, useCallback, useEffect } from 'react';
import { AlertData, FeedStatusData } from '@/lib/types';
import { WebSocketMessageType } from '@/lib/websocket/WebSocketClient';
import { useWebSocket } from '@/lib/websocket/WebSocketProvider';

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
    const client = useWebSocket();
    const [kpis, setKpis] = useState<KPIData | null>(null);
    const [alerts, setAlerts] = useState<AlertData[]>([]);
    const [feeds, setFeeds] = useState<FeedStatusData[]>([]);
    // const [nodeCongestionData, setNodeCongestionData] = useState<BackendCongestionNodeData[]>([]);
    const [isConnected, setIsConnected] = useState(client.isConnected());
    const [isReady, setIsReady] = useState(client.isConnected());
    const [error, setError] = useState<string | null>(null);
    const hasRequestedInitialFeeds = useRef(false);

    const sendMessage = useCallback((action: string, payload?: object): boolean => {
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
    }, [client]);

    const startWebSocket = useCallback(() => {
        // Connection is managed by WebSocketProvider
        // This is kept for compatibility, but acts as a no-op regarding connection initiation
        if (!client.isConnected()) {
             console.log("startWebSocket called. Client state:", client.getConnectionState());
        }
    }, [client]);

    useEffect(() => {
        const subscriptions: (() => void)[] = [];

        const updateConnectionState = () => {
            const connected = client.isConnected();
            setIsConnected(connected);
            setIsReady(connected);
            return connected;
        };

        const connected = updateConnectionState();
        
        if (connected && !hasRequestedInitialFeeds.current) {
            console.log("WebSocket already connected, requesting initial feed statuses.");
            client.send({ type: WebSocketMessageType.GET_INITIAL_FEED_STATUSES, data: {} });
            hasRequestedInitialFeeds.current = true;
        }

        console.log('Setting up WebSocket subscriptions in useRealtimeUpdates.');

        subscriptions.push(client.onStatusChange((status, message) => {
            console.log(`WebSocket status: ${status}`, message);
            const isNowConnected = status === 'connected';
            setIsConnected(isNowConnected);
            setIsReady(isNowConnected);

            if (isNowConnected) {
                console.log("WebSocket connected (event), requesting initial feed statuses.");
                client.send({ type: WebSocketMessageType.GET_INITIAL_FEED_STATUSES, data: {} });
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
            // Do NOT disconnect client here
            hasRequestedInitialFeeds.current = false; 
        };
    }, [client]);

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

    const restartFeed = useCallback((feedId: string) => {
        console.log(`Requesting to restart feed: ${feedId}`);
        sendMessage(WebSocketMessageType.RESTART_FEED, { feed_id: feedId });
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
        restartFeed,
        startWebSocket
    };
};
