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
        if (!webSocketClientRef.current) {
            const wsUrl = new URL('/api/v1/ws', process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000').toString();
            webSocketClientRef.current = new WebSocketClient(wsUrl);
            console.log('New WebSocket client created.');
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

        subscriptions.push(client.subscribe(WebSocketMessageType.VIDEO_FRAME, (data: { feed_id: string, frame: ArrayBuffer }) => {
            if (data && data.feed_id && data.frame) {
                setVideoFrames(prev => ({ ...prev, [data.feed_id]: data.frame }));
            }
        }));
        
        subscriptions.push(client.subscribe(WebSocketMessageType.KPI_UPDATE, (data: KPIData) => setKpis(data)));

        subscriptions.push(client.subscribe(WebSocketMessageType.NEW_ALERT, (data: { alert_data: AlertData }) => {
            if (data?.alert_data) {
                setAlerts(prevAlerts => [...prevAlerts, data.alert_data].slice(-10));
            }
        }));

        const connect = async () => {
            if (client.isConnected() || client.getConnectionState() === 'connecting') {
                // If we are already connected or connecting, we might still need to request initial feeds
                // if the previous attempt was interrupted by strict mode.
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
        };

        connect();

        return () => {
            console.log('Cleaning up WebSocket subscriptions. In React Strict Mode, this runs on unmount.');
            subscriptions.forEach(unsubscribe => unsubscribe());
            // We don't destroy the client here anymore.
            // We also don't disconnect, as another component (or the same one after remount) might need it.
            // Resetting hasRequestedInitialFeeds allows the remounted effect to request feeds again if needed.
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