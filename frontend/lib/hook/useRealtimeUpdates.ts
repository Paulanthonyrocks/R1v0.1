import { useEffect, useRef, useState, useCallback } from 'react';
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

export const useRealtimeUpdates = (url: string): RealtimeUpdates & { feeds: FeedStatusData[] } => {
  const [kpis, setKpis] = useState<KPIData | null>(null);
  const [alerts, setAlerts] = useState<AlertData[]>([]);
  const [feeds, setFeeds] = useState<FeedStatusData[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [isReady, setIsReady] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);

  const startWebSocket = useCallback(() => {
    if (wsRef.current) return; // Prevent multiple connections
    const ws = new WebSocket(url);
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
        if (message.event_type === 'FEED_STATUS_UPDATE') {
          const updatedFeed = message.payload.feed_status_data;
          setFeeds(prevFeeds => {
            const feedIndex = prevFeeds.findIndex(f => f.feed_id === updatedFeed.feed_id);
            if (feedIndex > -1) {
              const newFeeds = [...prevFeeds];
              newFeeds[feedIndex] = updatedFeed;
              return newFeeds;
            }
            return [...prevFeeds, updatedFeed];
          });
        } else if (message.payload && message.payload.feeds) {
          // This handles the initial bulk feed update
          setFeeds(message.payload.feeds);
        }

        if (message.kpis) setKpis(message.kpis);
        if (message.alerts) setAlerts(prevAlerts => [...prevAlerts, ...message.alerts]);

      } catch (error) {
        console.error('Error parsing WebSocket message:', error);
      }
    };
  }, [url]);

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
