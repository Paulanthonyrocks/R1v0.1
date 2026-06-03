
'use client';

import React, { createContext, useContext, useEffect, useRef, useState } from 'react';
import { WebSocketClient } from './WebSocketClient';
import { useAuth } from '../auth/AuthProvider';
import { getOrCreateWebSocketClient } from './websocketSingleton';

const WebSocketContext = createContext<WebSocketClient | null | undefined>(undefined);

export const useWebSocket = () => {
 const context = useContext(WebSocketContext);
 if (context === undefined) {
 throw new Error('useWebSocket must be used within a WebSocketProvider');
 }
 return context;
};

const getWsUrl = (path: string) => {
  const wsEnvUrl = process.env.NEXT_PUBLIC_WS_URL;
  if (wsEnvUrl) {
    const baseUrl = wsEnvUrl.replace(/\/$/, '');
    return `${baseUrl}${path.startsWith('/') ? path : '/' + path}`;
  }

  let httpBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!httpBaseUrl && typeof window !== 'undefined') {
    httpBaseUrl = window.location.origin;
  }
  httpBaseUrl = httpBaseUrl || 'http://localhost:8000';

  let baseUrlObj: URL;
  try {
    baseUrlObj = new URL(httpBaseUrl);
  } catch {
    if (httpBaseUrl.endsWith(':')) {
      httpBaseUrl += '//localhost';
    } else {
      httpBaseUrl = 'http://localhost:8000';
    }
    baseUrlObj = new URL(httpBaseUrl);
  }

  const wsProtocol = baseUrlObj.protocol === 'https:' ? 'wss:' : 'ws:';
  baseUrlObj.protocol = wsProtocol;
  const cleanPath = path.startsWith('/') ? path : '/' + path;
  return `${baseUrlObj.origin}${cleanPath}`;
};

const WS_BASE_URL = getWsUrl('/api/v1/ws');

export const WebSocketProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
 const { token, loading } = useAuth();
 const [client, setClient] = useState<WebSocketClient | null>(null);

 // Initialize the WebSocket client exactly once on mount.
 // This ensures a fresh instance per mount cycle and avoids singleton state issues.
 useEffect(() => {
   const clientInstance = getOrCreateWebSocketClient(WS_BASE_URL);
   setClient(clientInstance);
   clientInstance.activate();
   
   console.log(`[WebSocketProvider] Mounted. Client instance: ${clientInstance.getInstanceId()}`);

   return () => {
     console.log(`[WebSocketProvider] Unmounting. Connection persists via singleton.`);
   };
 }, []);

 useEffect(() => {
  if (loading) {
    return;
  }

  let isCurrent = true;
  if (!client) return;

  const handleConnection = async () => {
    // Race condition guard: ensure this timeout callback is still relevant
    if (!isCurrent) return;

    if (token) {
      if (!client.isConnected() && client.getConnectionState() !== 'connecting') {
        console.log(`[WebSocketProvider] Token available. Connecting instance: ${client.getInstanceId()}`);
        try {
          await client.connect(token);
        } catch (error) {
          console.error("WebSocket connection error on connect:", error);
        }
      }
    } else {
      if (client.getConnectionState() !== 'disconnected') {
        console.log(`[WebSocketProvider] No token. Client will remain disconnected: ${client.getInstanceId()}`);
      }
    }
  };

  // Debounce to allow token stabilization
  const debounceTimeout = setTimeout(handleConnection, 500);

  return () => {
    isCurrent = false;
    clearTimeout(debounceTimeout);
  };
}, [token, loading, client]);

 return (
 <WebSocketContext.Provider value={client}>
 {children}
 </WebSocketContext.Provider>
 );
};
