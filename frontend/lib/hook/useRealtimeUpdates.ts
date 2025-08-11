import { useEffect, useRef, useState, useCallback } from 'react';
import { AlertData, FeedStatusData, BackendCongestionNodeData } from '@/lib/types';
import { WebSocketClient, WebSocketMessageType } from '@/lib/websocket/WebSocketClient';

interface VehicleData {
    x1: number;
    y1: number;
    x2: number;
    y2: number;
    id: string;
    speed: number;
  }

interface KPIData {
  // Define the expected structure of your KPIs here
  vehicles: VehicleData[]; 
  vehicle_count: number;
  avg_speed: number;
  [key: string]: unknown;
}

interface RealtimeUpdates {
  kpis: KPIData | null;
  alerts: AlertData[];
  nodeCongestionData: BackendCongestionNodeData[]; // Added for WebSocket node congestion updates
  isConnected: boolean;
  isReady: boolean;
  error: string | null; // Added error property
  sendMessage: (action: string, payload?: object) => boolean; // Added sendMessage
  startWebSocket: () => void;
}

export const useRealtimeUpdates = (token: string | null): RealtimeUpdates & { feeds: FeedStatusData[] } => {
  const webSocketClientRef = useRef<WebSocketClient | null>(null);
  const [kpis, setKpis] = useState<KPIData | null>(null);
  const [alerts, setAlerts] = useState<AlertData[]>([]);
  const [feeds, setFeeds] = useState<FeedStatusData[]>([]);
  const [nodeCongestionData, setNodeCongestionData] = useState<BackendCongestionNodeData[]>([]); // Added nodeCongestionData state
  const [isConnected, setIsConnected] = useState(false);
  const [isReady, setIsReady] = useState(false);
  const [error, setError] = useState<string | null>(null); // Added error state

  const WS_BASE_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000';

  const startWebSocket = useCallback(() => {
    if (webSocketClientRef.current) {
      return; // Prevent multiple connections
    }

    const wsUrl = new URL('/api/v1/ws', WS_BASE_URL).toString();
    console.log('Constructed WebSocket URL:', wsUrl);
    const client = new WebSocketClient(wsUrl);
    webSocketClientRef.current = client;

    client.onError((type, message) => {
      console.error(`WebSocket Error (${type}):`, message);
      setIsConnected(false);
      setIsReady(false);
      setError(message); // Set the error state
    });

    client.subscribe(WebSocketMessageType.FEED_STATUS_UPDATE, (data: { feed_status_data: FeedStatusData }) => {
      const updatedFeed = data.feed_status_data;
      setFeeds(prevFeeds => {
        const feedIndex = prevFeeds.findIndex(f => f.id === updatedFeed.id);
        if (feedIndex > -1) {
          const newFeeds = [...prevFeeds];
          newFeeds[feedIndex] = updatedFeed;
          return newFeeds;
        }
        return [...prevFeeds, updatedFeed];
      });
    });

    client.subscribe(WebSocketMessageType.INITIAL_FEED_STATUSES, (data: { feeds: FeedStatusData[] }) => {
      setFeeds(data.feeds);
    });

    client.subscribe(WebSocketMessageType.GLOBAL_REALTIME_METRICS_UPDATE, (data: KPIData) => {
      setKpis(data);
    });

    client.subscribe(WebSocketMessageType.NEW_ALERT, (data: { alert_data: AlertData }) => {
      setAlerts(prevAlerts => [...prevAlerts, data.alert_data]);
    });

    client.subscribe(WebSocketMessageType.NODE_CONGESTION_UPDATE, (data: { nodes: BackendCongestionNodeData[] }) => {
      setNodeCongestionData(data.nodes);
    });

    // Add a general notification listener to track connection status
    client.subscribe(WebSocketMessageType.GENERAL_NOTIFICATION, (data: { message_type: string }) => {
      if (data.message_type === 'auth_success') {
        setIsConnected(true);
        setIsReady(true);
      }
    });

    // Connect the WebSocket client
    const currentToken = token;
    if (currentToken) {
      client.connect(currentToken);
    } else {
      // No token available, not connecting WebSocketClient.
    }

  }, [token, WS_BASE_URL]);

  useEffect(() => {
    if (token) {
      startWebSocket();
    }

    return () => {
      if (webSocketClientRef.current) {
        webSocketClientRef.current.disconnect();
        webSocketClientRef.current = null;
      }
    };
    return () => {
      if (webSocketClientRef.current) {
        webSocketClientRef.current.disconnect();
        webSocketClientRef.current = null;
      }
    };
  }, [token, startWebSocket]);

  const sendMessage = useCallback((action: string, payload?: object): boolean => {
    if (webSocketClientRef.current && isConnected) {
      webSocketClientRef.current.send({ type: action as WebSocketMessageType, data: payload });
      return true; // Message sent
    }
    console.warn('WebSocket not connected. Message not sent:', { action, payload });
    return false; // Message not sent
  }, [isConnected]);


  return { kpis, alerts, nodeCongestionData, isConnected, isReady, error, startWebSocket, feeds, sendMessage };
};
