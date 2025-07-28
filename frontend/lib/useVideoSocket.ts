import { useState, useEffect, useCallback } from 'react';
import { WebSocketClient, WebSocketMessageType } from './websocket/WebSocketClient';

interface KpiData {
  timestamp: string;
  vehicle_count: number;
  motion_level: number;
  frame_number: number;
}

const useVideoSocket = (streamId: string, token: string | null) => {
  const [kpis, setKpis] = useState<KpiData | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleKpiUpdate = useCallback((data: KpiData) => {
    setKpis(data);
  }, []);

  useEffect(() => {
    if (!token || !streamId) return;

    const wsClient = new WebSocketClient(`ws://localhost:8000/api/v1/video/ws/${streamId}?token=${token}`);

    const connect = async () => {
      try {
        await wsClient.connect(token);
        setIsConnected(true);
      } catch (err) {
        setError('Failed to connect to video WebSocket: ' + (err instanceof Error ? err.message : 'Unknown error'));
      }
    };

    connect();

    wsClient.subscribe(WebSocketMessageType.METRICS_UPDATE, handleKpiUpdate);

    return () => {
      wsClient.unsubscribe(WebSocketMessageType.METRICS_UPDATE, handleKpiUpdate);
      wsClient.disconnect();
    };
  }, [streamId, handleKpiUpdate, token]);

  return { kpis, isConnected, error };
};

export default useVideoSocket;
