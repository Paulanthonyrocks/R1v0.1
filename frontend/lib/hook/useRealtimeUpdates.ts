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
        const data = JSON.parse(event.data) as { kpis?: KPIData; alerts?: AlertData[]; feeds?: FeedStatusData[] };
        if (data.kpis) setKpis(data.kpis);
        if (data.alerts) setAlerts(data.alerts);
        if (data.feeds) setFeeds(data.feeds);
      } catch {
        // Ignore parse errors
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
