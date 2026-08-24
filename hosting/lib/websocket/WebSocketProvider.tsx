
'use client';

import React, { createContext, useContext, useEffect, useRef, useState } from 'react';
import { WebSocketClient } from './WebSocketClient';
import { useAuth } from '../auth/AuthProvider';
import { getOrCreateWebSocketClient } from './websocketSingleton';
import { getBackendWsURL } from '../api/backendBaseUrl';

const WebSocketContext = createContext<WebSocketClient | null | undefined>(undefined);

export const useWebSocket = () => {
 const context = useContext(WebSocketContext);
 if (context === undefined) {
  throw new Error('useWebSocket must be used within a WebSocketProvider');
 }
 return context;
};

// Backwards-compatible shim. New callers should use getBackendWsURL from
// lib/api/backendBaseUrl.ts directly — see ../api/backendBaseUrl.ts.
function legacyGetWsUrl(path: string): string {
  return getBackendWsURL(path);
}

const WS_BASE_URL = legacyGetWsUrl('/api/v1/ws');

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
        console.log(`[WebSocketProvider] No token. Disconnecting instance: ${client.getInstanceId()}`);
        // AUDIT FIX (2026-08-24): on logout the authenticated socket stayed open
        // with the old credential. Close it explicitly.
        client.disconnect();
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
