'use client';

import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { WebSocketClient, WebSocketMessageType } from '../websocket/WebSocketClient';
import { useWebSocket } from '../websocket/WebSocketProvider';
import { KpiData, AlertData, FeedStatusData } from '../types';

interface RealtimeState {
    kpis: KpiData | null;
    alerts: AlertData[];
    feeds: FeedStatusData[];
    nodeCongestionData: any[];
    isConnected: boolean;
    isReady: boolean;
    error: string | null;
}

interface RealtimeStateContextType extends RealtimeState {
    sendMessage: (action: WebSocketMessageType, payload?: object) => boolean;
    subscribeToFeed: (feedId: string) => void;
    unsubscribeFromFeed: (feedId: string) => void;
    startWebSocket: () => void;
}

const RealtimeStateContext = createContext<RealtimeStateContextType | null>(null);

interface RealtimeStateProviderProps {
    children: React.ReactNode;
}

export const RealtimeStateProvider = ({ children }: RealtimeStateProviderProps) => {
    const client = useWebSocket();
    const [kpis, setKpis] = useState<KpiData | null>(null);
    const [alerts, setAlerts] = useState<AlertData[]>([]);
    const [feeds, setFeeds] = useState<FeedStatusData[]>([]);
    const [nodeCongestionData, setNodeCongestionData] = useState<any[]>([]);
    const [isConnected, setIsConnected] = useState(() => client?.isConnected() ?? false);
    const [isReady, setIsReady] = useState(() => client?.isConnected() ?? false);
    const [error, setError] = useState<string | null>(null);

    const sendMessage = useCallback((action: WebSocketMessageType, payload?: object): boolean => {
        if (client && client.isConnected()) {
            try {
                client.send({ type: action, data: payload });
                return true;
            } catch (err) {
                console.error('Failed to send message:', err);
                return false;
            }
        }
        return false;
    }, [client]);

    const subscribeToFeed = useCallback((feedId: string) => {
        client?.send({ type: WebSocketMessageType.SUBSCRIBE_TO_FEED, data: { feed_id: feedId } });
    }, [client]);

    const unsubscribeFromFeed = useCallback((feedId: string) => {
        client?.send({ type: WebSocketMessageType.UNSUBSCRIBE_FROM_FEED, data: { feed_id: feedId } });
    }, [client]);

    const startWebSocket = useCallback(() => {
        if (!client?.isConnected()) {
            // Trigger connection attempt if needed
        }
    }, [client]);

    const updateConnectionState = useCallback(() => {
        const state = client?.getConnectionState() ?? 'disconnected';
        const connected = state === 'connected' || state === 'authenticated';
        setIsConnected(connected);
        setIsReady(state === 'authenticated');
    }, [client]);

    const initializeConnection = useCallback(async () => {
        if (!client) return;
        console.debug('[RealtimeStateProvider] Initializing connection subscriptions...');
        try {
            client.send({ 
                type: WebSocketMessageType.GET_INITIAL_FEED_STATUSES, 
                data: {} 
            });
            
            const topics = ['kpi', 'node_congestion'];
            topics.forEach(topic => {
                client.send({ 
                    type: WebSocketMessageType.SUBSCRIBE, 
                    data: { topic } 
                });
            });
            console.debug('[RealtimeStateProvider] Initial subscription requests sent.');
        } catch (e) {
            console.error('[RealtimeStateProvider] Failed to send initial subscriptions:', e);
        }
    }, [client]);

    useEffect(() => {
        const subscriptions: (() => void)[] = [];

        updateConnectionState();

        // Only initialize if already authenticated on mount
        if (client?.getConnectionState() === 'authenticated') {
            initializeConnection();
        }

        // Subscribe to server-side topics on every (re)connection
        const unsubStatus = client?.onStatusChange((status: string) => {
            const connected = status === 'connected' || status === 'authenticated';
            setIsConnected(connected);
            setIsReady(status === 'authenticated');

            if (status === 'authenticated') {
                initializeConnection();
            }
        });

        if (unsubStatus) subscriptions.push(unsubStatus);

        const unsubInitialFeeds = typeof client?.subscribe === 'function' 
            ? client.subscribe(WebSocketMessageType.INITIAL_FEED_STATUSES, (data: { feeds: FeedStatusData[] }) => {
                if (data && Array.isArray(data.feeds)) {
                    const sortedFeeds = [...data.feeds].sort((a, b) => a.feed_id.localeCompare(b.feed_id));
                    setFeeds(sortedFeeds);
                    sortedFeeds.forEach(feed => subscribeToFeed(feed.feed_id));
                }
            }) 
            : null;
        if (unsubInitialFeeds) subscriptions.push(unsubInitialFeeds);

        const unsubFeedUpdate = typeof client?.subscribe === 'function' 
            ? client.subscribe(WebSocketMessageType.FEED_STATUS_UPDATE, (data: { feed_status_data: FeedStatusData }) => {
                if (!data?.feed_status_data) return;
                const statusData = data.feed_status_data;
                setFeeds((prevFeeds: FeedStatusData[]) => {
                    const index = prevFeeds.findIndex((feed: FeedStatusData) => feed.feed_id === statusData.feed_id);
                    if (index !== -1) {
                        const existing = prevFeeds[index];
                        if (existing.status === statusData.status && JSON.stringify(existing.config) === JSON.stringify(statusData.config)) {
                            return prevFeeds;
                        }
                        const newFeeds = [...prevFeeds];
                        newFeeds[index] = statusData;
                        return newFeeds.sort((a, b) => a.feed_id.localeCompare(b.feed_id));
                    } else {
                        return [...prevFeeds, statusData].sort((a, b) => a.feed_id.localeCompare(b.feed_id));
                    }
                });
            }) 
            : null;
        if (unsubFeedUpdate) subscriptions.push(unsubFeedUpdate);

        const unsubKpi = typeof client?.subscribe === 'function' 
            ? client.subscribe(WebSocketMessageType.KPI_UPDATE, (data: KpiData) => {
                console.debug('[RealtimeStateProvider] Updating KPIs:', data);
                setKpis(data);
            }) 
            : null;
        if (unsubKpi) subscriptions.push(unsubKpi);

        const unsubCongestion = typeof client?.subscribe === 'function' 
            ? client.subscribe(WebSocketMessageType.NODE_CONGESTION_UPDATE, (data: { nodes: any[] }) => {
                if (data && Array.isArray(data.nodes)) {
                    setNodeCongestionData(data.nodes);
                }
            }) 
            : null;
        if (unsubCongestion) subscriptions.push(unsubCongestion);

        const unsubAlert = typeof client?.subscribe === 'function' 
            ? client.subscribe(WebSocketMessageType.NEW_ALERT, (data: { alert_data: AlertData }) => {
                if (data?.alert_data) {
                    setAlerts((prevAlerts: AlertData[]) => {
                        if (prevAlerts.some((a: AlertData) => a.id === data.alert_data.id)) return prevAlerts;
                        return [...prevAlerts, data.alert_data].slice(-20);
                    });
                }
            }) 
            : null;
        if (unsubAlert) subscriptions.push(unsubAlert);

        const unsubGen = typeof client?.subscribe === 'function' 
            ? client.subscribe(WebSocketMessageType.GENERAL_NOTIFICATION, (data: any) => {
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
                    setAlerts((prevAlerts: AlertData[]) => {
                        if (prevAlerts.some((a: AlertData) => a.id === newIncident.id)) return prevAlerts;
                        return [...prevAlerts, newIncident].slice(-20);
                    });
                } else if (data?.message_type === 'incident_update') {
                    setAlerts((prevAlerts: AlertData[]) => prevAlerts.map((alert: AlertData) =>
                        alert.id === data.incident_id || String(alert.id) === String(data.incident_id)
                            ? { ...alert, status: data.status, resolution_notes: data.notes, updated_at: new Date().toISOString() }
                            : alert
                    ));
                }
            }) 
            : null;
        if (unsubGen) subscriptions.push(unsubGen);

        const unsubError = client?.onError((_type: string, message: string) => setError(message));
        if (unsubError) subscriptions.push(unsubError);

        return () => {
            subscriptions.forEach(unsubscribe => unsubscribe());
        };
    }, [client, subscribeToFeed, updateConnectionState, initializeConnection]);

    const value = React.useMemo(() => ({
        kpis,
        alerts,
        feeds,
        nodeCongestionData,
        isConnected,
        isReady,
        error,
        sendMessage,
        subscribeToFeed,
        unsubscribeFromFeed,
        startWebSocket
    }), [kpis, alerts, feeds, nodeCongestionData, isConnected, isReady, error, sendMessage, subscribeToFeed, unsubscribeFromFeed, startWebSocket]);

    return (
        <RealtimeStateContext.Provider value={value}>
            {children}
        </RealtimeStateContext.Provider>
    );
};

export const useRealtimeState = () => {
    const context = useContext(RealtimeStateContext);
    if (!context) {
        throw new Error('useRealtimeState must be used within a RealtimeStateProvider');
    }
    return context;
};
