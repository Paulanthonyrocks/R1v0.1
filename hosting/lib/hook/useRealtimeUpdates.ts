import { useRef, useState, useCallback, useEffect } from 'react';
import { AlertData, FeedStatusData, BackendCongestionNodeData } from '@/lib/types';
import { WebSocketMessageType } from '@/lib/websocket/WebSocketClient';
import { useWebSocket } from '@/lib/websocket/WebSocketProvider';

interface KPIData {
    timestamp?: string | Date; // Matches backend GlobalRealtimeMetrics
    metrics_source?: string; // Matches backend GlobalRealtimeMetrics
    congestion_index?: number | null; // Matches backend GlobalRealtimeMetrics
    average_speed_kmh?: number | null; // Matches backend GlobalRealtimeMetrics
    active_incidents_count?: number | null; // Matches backend GlobalRealtimeMetrics
    total_flow?: number | null; // Matches backend GlobalRealtimeMetrics
    global_health_score?: number | null; // Matches backend GlobalRealtimeMetrics
    feed_statuses?: { [key: string]: number } | null; // Matches backend GlobalRealtimeMetrics
    custom_metrics?: { [key: string]: unknown }; // Matches backend GlobalRealtimeMetrics
    [key: string]: unknown; // Allow for other potential metrics not explicitly defined
}

interface RealtimeUpdates {
    kpis: KPIData | null;
    alerts: AlertData[];
    nodeCongestionData: BackendCongestionNodeData[];
    isConnected: boolean;
    isReady: boolean;
    error: string | null;
    sendMessage: (action: string, payload?: object) => boolean;
    subscribeToFeed: (feedId: string) => void;
    unsubscribeFromFeed: (feedId: string) => void;
}

export const useRealtimeUpdates = (): RealtimeUpdates & {
    feeds: FeedStatusData[],
    startFeed: (feedId: string) => void,
    stopFeed: (feedId: string) => void,
    restartFeed: (feedId: string) => void,
    startWebSocket: () => void
} => {
    const client = useWebSocket();
    const [kpis, setKpis] = useState<KPIData | null>(null);
    const [alerts, setAlerts] = useState<AlertData[]>([]);
    const [feeds, setFeeds] = useState<FeedStatusData[]>([]);
    const [nodeCongestionData, setNodeCongestionData] = useState<BackendCongestionNodeData[]>([]);
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
            console.log(`[useRealtimeUpdates] updateConnectionState: connected=${connected}`);
            setIsConnected(connected);
            setIsReady(connected);
            return connected;
        };

        const connected = updateConnectionState();

        if (connected && !hasRequestedInitialFeeds.current) {
            console.log("[useRealtimeUpdates] WebSocket already connected on mount, requesting initial feed statuses.");
            client.send({ type: WebSocketMessageType.GET_INITIAL_FEED_STATUSES, data: {} });
            hasRequestedInitialFeeds.current = true;
        }

        console.log(`[useRealtimeUpdates] Setting up WebSocket subscriptions. Client instance: ${client.getInstanceId()}`);

        subscriptions.push(client.onStatusChange((status, message) => {
            console.log(`[useRealtimeUpdates] WebSocket status change: ${status}`, message);
            const isNowConnected = status === 'connected';
            setIsConnected(isNowConnected);
            setIsReady(isNowConnected);

            if (isNowConnected) {
                if (!hasRequestedInitialFeeds.current) {
                    console.log("[useRealtimeUpdates] WebSocket connected (event), requesting initial feed statuses and subscribing to topics.");
                    client.send({ type: WebSocketMessageType.GET_INITIAL_FEED_STATUSES, data: {} });
                    client.send({ type: WebSocketMessageType.SUBSCRIBE, data: { topic: 'kpi' } });
                    client.send({ type: WebSocketMessageType.SUBSCRIBE, data: { topic: 'node_congestion' } });
                    hasRequestedInitialFeeds.current = true;
                }
            } else {
                // Reset flag on disconnection so it requests again on reconnect
                hasRequestedInitialFeeds.current = false;
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

        subscriptions.push(client.subscribe(WebSocketMessageType.FEED_STATUS_UPDATE, (data: { feed_status_data: FeedStatusData }) => {
            console.log("Received FEED_STATUS_UPDATE data:", JSON.stringify(data));
            if (!data?.feed_status_data) {
                console.warn("Received FEED_STATUS_UPDATE but no feed_status_data found:", data);
                return;
            }
            const statusData = data.feed_status_data;
            setFeeds(prevFeeds => {
                const index = prevFeeds.findIndex(feed => feed.feed_id === statusData.feed_id);
                if (index !== -1) {
                    const newFeeds = [...prevFeeds];
                    newFeeds[index] = statusData;
                    return newFeeds;
                }
                return [...prevFeeds, statusData];
            });
        }));

        subscriptions.push(client.subscribe(WebSocketMessageType.KPI_UPDATE, (data: KPIData) => setKpis(data)));

        subscriptions.push(client.subscribe(WebSocketMessageType.NODE_CONGESTION_UPDATE, (data: { nodes: BackendCongestionNodeData[] }) => {
            if (data && Array.isArray(data.nodes)) {
                setNodeCongestionData(data.nodes);
            }
        }));

        subscriptions.push(client.subscribe(WebSocketMessageType.NEW_ALERT, (data: { alert_data: AlertData }) => {
            if (data?.alert_data) {
                setAlerts(prevAlerts => {
                    // Prevent duplicates if also receiving as GENERAL_NOTIFICATION
                    if (prevAlerts.some(a => a.id === data.alert_data.id)) return prevAlerts;
                    return [...prevAlerts, data.alert_data].slice(-20);
                });
            }
        }));

        subscriptions.push(client.subscribe(WebSocketMessageType.GENERAL_NOTIFICATION, (data: any) => {
            if (data?.message_type === 'new_incident') {
                const newIncident: AlertData = {
                    id: data.incident_id,
                    timestamp: new Date(),
                    severity: data.severity,
                    feed_id: data.feed_id,
                    message: data.title || "New Incident",
                    description: data.message,
                    status: 'REPORTED'
                };
                setAlerts(prevAlerts => {
                    if (prevAlerts.some(a => a.id === newIncident.id)) return prevAlerts;
                    return [...prevAlerts, newIncident].slice(-20);
                });
            } else if (data?.message_type === 'incident_update') {
                setAlerts(prevAlerts => prevAlerts.map(alert =>
                    alert.id === data.incident_id || String(alert.id) === String(data.incident_id)
                        ? { ...alert, status: data.status, resolution_notes: data.notes, updated_at: new Date().toISOString() }
                        : alert
                ));
            }
        }));

        return () => {
            console.log('Cleaning up WebSocket subscriptions. In React Strict Mode, this runs on unmount.');
            subscriptions.forEach(unsubscribe => unsubscribe());
            // Do NOT disconnect client here
        };
    }, [client]);

    const subscribeToFeed = useCallback((feedId: string) => {
        console.log(`Requesting to subscribe to feed: ${feedId}`);
        sendMessage(WebSocketMessageType.SUBSCRIBE_TO_FEED, { feed_id: feedId });
    }, [sendMessage]);

    const unsubscribeFromFeed = useCallback((feedId: string) => {
        console.log(`Requesting to unsubscribe from feed: ${feedId}`);
        sendMessage(WebSocketMessageType.UNSUBSCRIBE_FROM_FEED, { feed_id: feedId });
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
        nodeCongestionData, 
        isConnected,
        isReady,
        error,
        feeds,
        sendMessage,
        subscribeToFeed,
        unsubscribeFromFeed,
        startFeed,
        stopFeed,
        restartFeed,
        startWebSocket
    };
};
