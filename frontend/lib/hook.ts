// lib/hooks.ts
import { useEffect, useRef, useState, useCallback } from 'react';
import {
    FeedStatusData, KpiData, AlertData, KpiUpdatePayload, UseRealtimeUpdatesReturn,
} from '@/lib/types';

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000/ws';
const INITIAL_RECONNECT_DELAY_MS = 2000; // Start delay at 2s
const MAX_RECONNECT_DELAY_MS = 30000; // Max delay 30s
const MAX_RECONNECT_ATTEMPTS = 5;     // Max attempts before stopping

export const useRealtimeUpdates = (): UseRealtimeUpdatesReturn => {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null);
  const reconnectAttempts = useRef(0); // Track attempts
  const lastPingRef = useRef<number>(0);

  const [isConnected, setIsConnected] = useState(false);
  const [feeds, setFeeds] = useState<FeedStatusData[]>([]);
  const [kpis, setKpis] = useState<KpiData | null>(null);
  const [alerts, setAlerts] = useState<AlertData[]>([]);
  const [error, setError] = useState<string | null>(null);

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
    };

    ws.onmessage = (event) => {
        // (Message handling logic - unchanged from previous correct version)
        try {
            const message = JSON.parse(event.data);
            switch (message.type) {

              case 'feed_update':
                setFeeds((prevFeeds: FeedStatusData[]) => {
                  const updatedFeeds = (prevFeeds as FeedStatusData[]).map((feed: FeedStatusData) =>
                      feed.id === message.data.id
                          ? { ...feed, status: message.data.status }
                          : feed
                  );
                  // Ensure a new array is created to trigger re-renders
                  return [...updatedFeeds];
                });
                console.debug(`Feed ${message.data.id} updated to status: ${message.data.status}`);

                break;
              case 'kpi_update':
                 setKpis(message.data as KpiUpdatePayload);
                break;

              case 'new_alert': {
                const newAlert: AlertData = message.data;
                setAlerts((prevAlerts) => [newAlert, ...prevAlerts]);
                console.debug('Received new alert:', newAlert);
                }
                break;

              case 'error':
                  // Handle error messages from the backend
                  console.error('Received error from backend:', message.message);
                  if (error !== message.message) { // Only update if the message is different
                    setError(message.message);
                  }
                  break;

              case 'ping':
                  console.debug('Received ping, sending pong...');
                  ws.send(JSON.stringify({ type: 'pong' }));
                  lastPingRef.current = Date.now();
                  break;

              default:
                console.warn('Received unknown WebSocket message type:', message.type);
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
      wsRef.current = null; // Clear the ref

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
  }, []); // Dependency array is empty, connectWebSocket is stable
  
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


    // Function to send messages, checking for open connection
    const sendMessage = useCallback((action: string, payload: object = {}) => {
      if (isConnected && wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        const message = {
          type: action,
          data: payload,
        };
        wsRef.current.send(JSON.stringify(message));
        console.debug(`Sent message: Type=${action}, Data=${JSON.stringify(payload)}`);
        return true; // Indicate message was sent
      } else {
        console.warn(`WebSocket is not open. Cannot send message: Type=${action}, Data=${JSON.stringify(payload)}`);
        setError('Attempted to send message while disconnected. Reconnecting...');
        connectWebSocket();  // Attempt to reconnect
        return false; // Indicate message was not sent
      }
    }, [isConnected, connectWebSocket]);

  return {
    isConnected,
    feeds,
    kpis,
    alerts,
    error,
    setInitialFeeds,
    setInitialAlerts,
    startWebSocket: connectWebSocket, // Expose connectWebSocket as startWebSocket
    sendMessage,
  };
};