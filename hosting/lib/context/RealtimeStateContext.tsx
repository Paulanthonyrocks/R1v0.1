'use client';

import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { WebSocketClient, WebSocketMessageType } from '../websocket/WebSocketClient';
import { useWebSocket } from '../websocket/WebSocketProvider';
import { KPIData, AlertData, FeedStatusData } from '../types/api';

interface RealtimeState {
    kpis: KPIData | null;
    alerts: AlertData[];
    feeds: FeedStatusData[];
    nodeCongestionData: any[];
    isConnected: boolean;
    isReady: boolean;
    error: string | null;
}

interface RealtimeStateContextType extends RealtimeState {
    sendMessage: (action: string, payload?: object) => boolean;
    subscribeToFeed: (feedId: string) => void;
    unsubscribeFromFeed: (feedId: string) => void;
    startWebSocket: () => void;
}

const RealtimeStateContext = createContext<RealtimeStateContextType | null>(null);

export const RealtimeStateProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
    const client = useWebSocket();
    const [kpis, setKpis] = useState<KPIData | null>(null);
    const [alerts, setAlerts] = useState<AlertData[]>([]);
    const [feeds, setFeeds] = useState<FeedStatusData[]>([]);
    const [nodeCongestionData, setNodeCongestionData] = useState<any[]>([]);
    const [isConnected, setIsConnected] = useState(client.isConnected());
    const [isReady, setIsReady] = useState(client.isConnected());
    const [error, setError] = useState<string | null>(null);

    const sendMessage = useCallback((action: string, payload?: object): boolean => {
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
        client.send({ type: 'SUBSCRIBE_FEED', data: { feed_id: feedId } });
    }, [client]);

    const unsubscribeFromFeed = useCallback((feedId: string) => {
        client.send({ type: 'UNSUBSCRIBE_FEED', data: { feed_id: feedId } });
    }, [client]);

    const startWebSocket = useCallback(() => {
        if (!client.isConnected()) {
            // Trigger connection attempt if needed
        }
    }, [client]);

    useEffect(() => {
        const subscriptions: (() => void)[] = [];

        const updateConnectionState = () => {
            const connected = client.isConnected();
            setIsConnected(connected);
            setIsReady(connected);
        };

        updateConnectionState();

        subscriptions.push(client.subscribe(WebSocketMessageType.INITIAL_FEED_STATUSES, (data: { feeds: FeedStatusData[] }) => {
            if (data && Array.isArray(data.feeds)) {
                const sortedFeeds = [...data.feeds].sort((a, b) => a.feed_id.localeCompare(b.feed_id));
                setFeeds(sortedFeeds);
                sortedFeeds.forEach(feed => subscribeToFeed(feed.feed_id));
            }
        }));

        subscriptions.push(client.subscribe(WebSocketMessageType.FEED_STATUS_UPDATE, (data: { feed_status_data: FeedStatusData }) => {
            if (!data?.feed_status_data) return;
            const statusData = data.feed_status_data;
            setFeeds(prevFeeds => {
                const index = prevFeeds.findIndex(feed => feed.feed_id === statusData.feed_id);
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
        }));

        subscriptions.push(client.subscribe(WebSocketMessageType.KPI_UPDATE, (data: KPIData) => setKpis(data)));

        subscriptions.push(client.subscribe(WebSocketMessageType.NODE_CONGESTION_UPDATE, (data: { nodes: any[] }) => {
            if (data && Array.isArray(data.nodes)) {
                setNodeCongestionData(data.nodes);
            }
        }));

        subscriptions.push(client.subscribe(WebSocketMessageType.NEW_ALERT, (data: { alert_data: AlertData }) => {
            if (data?.alert_data) {
                setAlerts(prevAlerts => {
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

        subscriptions.push(client.onError((_type, message) => setError(message)));

        return () => {
            subscriptions.forEach(unsubscribe => unsubscribe());
        };
    }, [client, subscribeToFeed]);

    const value = {
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
    };

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
