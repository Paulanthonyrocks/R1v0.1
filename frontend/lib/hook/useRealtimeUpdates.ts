import { useEffect, useRef, useState, useCallback } from 'react';
import { v4 as uuidv4 } from 'uuid';
import { AlertData, FeedStatusData } from '@/lib/types';

interface KPIData {
  // Define the expected structure of your KPIs here
  [key: string]: unknown;
}

interface RealtimeUpdates {
  kpis: KPIData | null;
  alerts: AlertData[];
  isConnected: boolean;
  isReady: boolean;
  startWebSocket: () => void;
}

export const useRealtimeUpdates = (token: string | null): RealtimeUpdates & { feeds: FeedStatusData[] } => {
  const WS_BASE_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000';
  const CLIENT_ID = typeof window !== 'undefined' ? (window.localStorage.getItem('client_id') || (() => { const id = uuidv4(); window.localStorage.setItem('client_id', id); return id; })()) : 'default-client';
  const wsUrl = token ? `${WS_BASE_URL.replace(/\/$/, '')}/ws/${CLIENT_ID}?token=${token}` : null;
  const [kpis, setKpis] = useState<KPIData | null>(null);
  const [alerts, setAlerts] = useState<AlertData[]>([]);
  const [feeds, setFeeds] = useState<FeedStatusData[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [isReady, setIsReady] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  const startWebSocket = useCallback(() => {
    if (wsRef.current || !wsUrl) return; // Prevent multiple connections or if wsUrl is null
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    ws.onopen = () => {
      setIsConnected(true);
      setIsReady(true);
    };
    ws.onclose = () => {
      setIsConnected(false);
      setIsReady(false);
      wsRef.current = null;
    };
    ws.onerror = () => {
      setIsConnected(false);
      setIsReady(false);
    };
    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === 'feed_status_update') {
          const updatedFeed = message.data.feed_status_data;
          setFeeds(prevFeeds => {
            const feedIndex = prevFeeds.findIndex(f => f.feed_id === updatedFeed.feed_id);
            if (feedIndex > -1) {
              const newFeeds = [...prevFeeds];
              newFeeds[feedIndex] = updatedFeed;
              return newFeeds;
            }
            return [...prevFeeds, updatedFeed];
          });
        } else if (message.type === 'initial_feed_statuses') {
          // This handles the initial bulk feed update
          setFeeds(message.data.feeds);
        } else if (message.type === 'global_realtime_metrics_update') {
          setKpis(message.data);
        } else if (message.type === 'new_alert') {
          setAlerts(prevAlerts => [...prevAlerts, message.data.alert_data]);
        }

      } catch (error) {
        console.error('Error parsing WebSocket message:', error);
      }
    };
  }, [wsUrl]);

  useEffect(() => {
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, []);

  return { kpis, alerts, isConnected, isReady, startWebSocket, feeds };
};
