// lib/hooks.ts
import { useEffect, useRef, useState, useCallback } from 'react';
import {
    FeedStatusData, KpiData, AlertData, BackendCongestionNodeData, // Added BackendCongestionNodeData
} from '@/lib/types';

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000/ws'; // Ensure this is ws://localhost:9002/ws if backend is on 9002
const INITIAL_RECONNECT_DELAY_MS = 2000; // Start delay at 2s
const MAX_RECONNECT_DELAY_MS = 30000; // Max delay 30s
const MAX_RECONNECT_ATTEMPTS = 6;     // Max attempts before stopping

type RealtimeData = {
    isConnected: boolean;
    feeds: FeedStatusData[];
    kpis: KpiData | null;
    alerts: AlertData[];
    nodeCongestionData: BackendCongestionNodeData[]; // Added nodeCongestionData
    error: string | null;
    isReady: boolean;
    setInitialFeeds: (feeds: FeedStatusData[]) => void;
    setInitialKpis: (kpis: KpiData) => void; // Keep if SWR is used for initial KPI load
    setInitialAlerts: (alerts: AlertData[]) => void;
    startWebSocket: () => void;
    getStreamInfo: (streamId: string) => { status: string; liveUrl: string | null } | undefined;
};

type MessageSender = (type: string, data?: unknown) => void;

export function useRealtimeUpdates(): RealtimeData & { sendMessage: MessageSender } {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const reconnectAttempts = useRef(0); // Track attempts
  const lastPingRef = useRef<number>(0);

  // Add WebSocketMessageTypeEnum if not already globally available or imported from a shared types location
  // For this example, assuming it might be defined or imported elsewhere, or we define it here.
  // Let's assume it's available via an import if this hook is part of a larger system.
  // If not, it would need to be defined or imported, e.g.:
  // import { WebSocketMessageTypeEnum } from '@/lib/types'; // if it were moved there

  const [isConnected, setIsConnected] = useState(false);
  const [feeds, setFeeds] = useState<FeedStatusData[]>([]);
  const [kpis, setKpis] = useState<KpiData | null>(null);
  const [alerts, setAlerts] = useState<AlertData[]>([]);
  const [nodeCongestionData, setNodeCongestionData] = useState<BackendCongestionNodeData[]>([]); // Added state for node congestion
  const [error, setError] = useState<string | null>(null);
  const [isReady, setIsReady] = useState(false);
  const readyTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const [streamInfoMap, setStreamInfoMap] = useState<Record<string, { status: string; liveUrl: string | null }>>({});

  // Memoized connection function - now private
  const connectWebSocket = useCallback(() => {
    // Clear any pending reconnect timeout
    if (reconnectTimeoutRef.current) {
      clearTimeout(reconnectTimeoutRef.current);
      reconnectTimeoutRef.current = null;
    }

    // Prevent multiple connections or reconnect attempts after max retries
    if (wsRef.current && wsRef.current.readyState < WebSocket.CLOSING) {
        console.log('WebSocket connection already open or opening.');
        return;
    }
    if (reconnectAttempts.current >= MAX_RECONNECT_ATTEMPTS) {
        console.warn(`WebSocket: Max reconnect attempts (${MAX_RECONNECT_ATTEMPTS}) reached. Stopping.`);
        setError(`Could not connect after ${MAX_RECONNECT_ATTEMPTS} attempts. Please refresh or check the server.`);
        return; // Stop trying
    }

    console.log(`Attempting WebSocket connection to ${WS_URL} (Attempt ${reconnectAttempts.current + 1})...`);
    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log('WebSocket connected successfully.');
      setIsConnected(true);
      setError(null); // Clear error on successful connection
      reconnectAttempts.current = 0; // Reset attempts on successful connection
      lastPingRef.current = Date.now();
      
      // Set ready state after a short delay
      readyTimeoutRef.current = setTimeout(() => {
          setIsReady(true);
      }, 500); // 500ms delay
    };

    ws.onmessage = (event) => {
        // (Message handling logic - unchanged from previous correct version)
        // Assuming WebSocketMessageTypeEnum is available, e.g. imported or defined above
        // For example:
        // enum WebSocketMessageTypeEnum { ... ALERT_STATUS_UPDATE = "alert_status_update", ... }

        try {
            const message = JSON.parse(event.data as string);
            // Ensure message.type is compared against defined enum values if using TypeScript enums
            // For string comparison as used in backend:
            switch (message.type) {
                case 'initial_data': // Assuming 'initial_data' is a string literal type
                    setFeeds(message.data.feeds);
 setKpis(message.data.kpis);
 setAlerts(message.data.alerts);
 setIsReady(true);
 // Clear the ready timeout if data is received
 if (readyTimeoutRef.current) {
 clearTimeout(readyTimeoutRef.current);
 readyTimeoutRef.current = null;
 }
 break;
 case 'feed_update':
 setFeeds(prevFeeds => {
 const index = prevFeeds.findIndex(feed => feed.id === message.data.id);
 if (index > -1) {
 const newFeeds = [...prevFeeds];
 newFeeds[index] = message.data;
 return newFeeds;
 } else {
 return [...prevFeeds, message.data];
 }
 });
 break;
 case 'kpi_update':
 setKpis(prevKpis => ({ ...prevKpis, ...message.data }));
 break;
 case 'alert_update':
 setAlerts(prevAlerts => {
 const existingAlertIndex = prevAlerts.findIndex(alert => alert.id === message.data.id);
 if (existingAlertIndex > -1) {
 const updatedAlerts = [...prevAlerts];
 updatedAlerts[existingAlertIndex] = message.data;
 return updatedAlerts;
 } else {
 return [...prevAlerts, message.data];
 }
 });
 break;
 case 'stream_update':
                  const streamUpdateData = message.data as { streamId: string; info: { status: string; liveUrl: string | null } };
                    setStreamInfoMap(prevStreamInfoMap => ({
                        ...prevStreamInfoMap,
                        [streamUpdateData.streamId]: streamUpdateData.info,
                    }));
                    break;
                case 'alert_status_update': // Matches backend WebSocketMessageTypeEnum.ALERT_STATUS_UPDATE
                    const statusUpdatePayload = message.payload as { alert_id: string | number; status: string; timestamp: string };
                    setAlerts(prevAlerts => {
                        const alertIdToUpdate = String(statusUpdatePayload.alert_id);
                        if (statusUpdatePayload.status === "dismissed") {
                            return prevAlerts.filter(alert => String(alert.id) !== alertIdToUpdate);
                        }
                        return prevAlerts.map(alert => {
                            if (String(alert.id) === alertIdToUpdate) {
                                return {
                                    ...alert,
                                    acknowledged: statusUpdatePayload.status === "acknowledged",
                                };
                            }
                            return alert;
                        });
                    });
                    break;
                case 'node_congestion_update': // Matches backend WebSocketMessageTypeEnum.NODE_CONGESTION_UPDATE
                    // Assuming message.payload is NodeCongestionUpdatePayload { nodes: BackendCongestionNodeData[] }
                    if (message.payload && Array.isArray(message.payload.nodes)) {
                        setNodeCongestionData(message.payload.nodes);
                    } else {
                        console.warn('Received node_congestion_update without valid nodes payload:', message.payload);
                    }
                    break;
                case 'error': // Assuming 'error' is a string literal type
                    setError(message.data.message); // Ensure message.data exists and has message
                    console.error('WebSocket Error from Server:', message.data?.message || message.payload?.message || 'Unknown server error');
                    break;
                case 'ping': // Assuming 'ping' is a string literal type
                    ws.send(JSON.stringify({ type: 'pong' }));
                    lastPingRef.current = Date.now();
                    break;
                default:
                    console.warn('Unknown message type:', message.type);
                    break;
            }

        } catch (e) {
            console.error('Failed to parse WebSocket message or update state:', e);
            setError('Error processing real-time updates.'); // Set a generic error
        }
    };

    ws.onerror = (event) => {
      console.error('WebSocket error event occurred:', event);
      // Error doesn't always trigger close, but we'll handle reconnect in onclose
      // setError(`WebSocket error occurred.`); // Optionally set error immediately
      setIsConnected(false); // Assume disconnect on error
    };

    ws.onclose = (event) => {
      console.log(`WebSocket disconnected: Code=${event.code}, Reason=${event.reason}`);
      setIsConnected(false);
      setIsReady(false);
      wsRef.current = null; // Clear the ref
      if (readyTimeoutRef.current) {
          clearTimeout(readyTimeoutRef.current);
      }

      // Attempt reconnect if not intentionally closed and max attempts not reached
      if (event.code !== 1000 && event.code !== 1005 && reconnectAttempts.current < MAX_RECONNECT_ATTEMPTS) {
        reconnectAttempts.current += 1;
        // Exponential backoff calculation
        const delay = Math.min(
            INITIAL_RECONNECT_DELAY_MS * Math.pow(2, reconnectAttempts.current - 1),
            MAX_RECONNECT_DELAY_MS
        );
        const errMsg = `WebSocket disconnected (Code: ${event.code}). Retrying connection ${reconnectAttempts.current}/${MAX_RECONNECT_ATTEMPTS} in ${Math.round(delay / 1000)}s...`;
        console.warn(errMsg);
        setError(errMsg);

        reconnectTimeoutRef.current = setTimeout(connectWebSocket, delay);

      } else if (reconnectAttempts.current >= MAX_RECONNECT_ATTEMPTS) {
          // Already handled in the check at the start of connectWebSocket
          console.warn("WebSocket: Max reconnect attempts reached on close event.");
      } else {
         // Clean disconnect (code 1000 or 1005)
         setError(null);
         reconnectAttempts.current = 0; // Reset attempts on clean close
         console.log("WebSocket closed cleanly.");
      }
    };
  }, []); // Remove error dependency
  
  // Heartbeat monitoring effect
  useEffect(() => {
    if (isConnected) {
      const heartbeatInterval = setInterval(() => {
        const now = Date.now();
        if (now - lastPingRef.current > 40000) {
          console.warn('No ping received from backend in 40 seconds. Assuming connection lost.');
          setIsConnected(false);
          wsRef.current?.close(1000, 'Heartbeat timeout'); // Clean close for reconnection
        }
      }, 10000); // Check every 10 seconds

      return () => clearInterval(heartbeatInterval); // Clear interval on disconnect
    }
  }, [isConnected]);


  // Cleanup Effect
  useEffect(() => {
    console.log('useWebSocket hook mounted.  Waiting for startWebSocket() call...');
    // Initial connection is now triggered by startWebSocket

    return () => {
      // Cleanup: clear timeouts and close WebSocket connection
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (wsRef.current) {
        console.log('Closing WebSocket connection on component unmount.');
        // Prevent automatic reconnect attempts when explicitly unmounting
        reconnectAttempts.current = MAX_RECONNECT_ATTEMPTS + 1; // Set above limit
        wsRef.current.close(1000, "Component unmounted");
        wsRef.current = null;
      }
    };
  }, []); // Only run on mount/unmount

  // Functions to set initial data from SWR (unchanged)
  const setInitialFeeds = useCallback((initialFeeds: FeedStatusData[]) => {
     // Add name mapping for UI consistency
     setFeeds(initialFeeds.map(f => ({ ...f, name: f.name || f.source })));
     console.log(`Initial feeds set (${initialFeeds.length}) from API data.`);
  }, []);
  const setInitialAlerts = useCallback((initialAlerts: AlertData[]) => {
     setAlerts(initialAlerts);
     console.log(`Initial alerts set (${initialAlerts.length}) from API data.`);
  }, []);

  const setInitialKpis = useCallback((initialKpis: KpiData) => {
    setKpis(initialKpis);
    console.log('Initial KPIs set from API data.');
  }, []);

    // Modified sendMessage function
    const sendMessage = useCallback((type: string, data: unknown = {}) => {
        if (!isConnected || !wsRef.current || !isReady) {
            console.warn('WebSocket is not ready. Cannot send message:', { type, data });
            return;
        }
        if (wsRef.current.readyState !== WebSocket.OPEN) {
            console.warn('WebSocket connection is not in OPEN state. Cannot send message:', { type, data });
            return;
        }
        wsRef.current.send(JSON.stringify({ type, data }));
    }, [isConnected, isReady]);

    // New function to get stream info
    const getStreamInfo = useCallback((streamId: string) => {
        return streamInfoMap[streamId];
    }, [streamInfoMap]);

  return {
    isConnected,
    feeds,
    kpis,
    alerts,
    nodeCongestionData, // Return nodeCongestionData
    error,
    isReady,
    setInitialFeeds,
    setInitialKpis,
    setInitialAlerts,
    getStreamInfo,
    startWebSocket: connectWebSocket, // Expose connectWebSocket as startWebSocket
    sendMessage,
  };
};