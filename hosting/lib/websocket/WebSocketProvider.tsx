
'use client';

import React, { createContext, useContext, useEffect, useMemo, useRef } from 'react';
import { WebSocketClient } from './WebSocketClient';
import { useAuth } from '../auth/AuthProvider';

const WebSocketContext = createContext<WebSocketClient | null>(null);

export const useWebSocket = () => {
 const context = useContext(WebSocketContext);
 if (!context) {
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

 const wsBaseUrl = httpBaseUrl.replace(/^(http)/, 'ws');
 
 return `${wsBaseUrl.replace(/\/$/, '')}${path}`;
}

const WS_BASE_URL = getWsUrl('/api/v1/ws');

export const WebSocketProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
 const { token, loading } = useAuth();

 const webSocketClient = useMemo(() => {
 return new WebSocketClient(WS_BASE_URL);
 }, []);

 // Debounce destroy() so React StrictMode's unmount→remount cycle
 // (which happens synchronously within a single tick) doesn't tear
 // down the entire WebSocket client, worker, and listeners.
 const destroyTimerRef = useRef<NodeJS.Timeout | null>(null);
 const isDestroyedRef = useRef(false);

 useEffect(() => {
 // Cancel any pending destroy from a previous StrictMode unmount
 if (destroyTimerRef.current) {
 clearTimeout(destroyTimerRef.current);
 destroyTimerRef.current = null;
 }

 if (isDestroyedRef.current) {
 // Client was previously destroyed — need to re-activate.
 // This handles the rare case where the Provider truly unmounted
 // and then re-mounted (not just StrictMode cycling).
 isDestroyedRef.current = false;
 }

 webSocketClient.activate();
 console.log(`[WebSocketProvider] Mounted. Client instance: ${webSocketClient.getInstanceId()}`);

 return () => {
 // Instead of destroying immediately, debounce by 150ms.
 // React StrictMode unmount→remount happens synchronously within
 // a single microtask, so 150ms is more than enough to skip it.
 // If the Provider truly unmounts (page navigation, etc.), the
 // timer fires and cleanup happens properly.
 destroyTimerRef.current = setTimeout(() => {
 isDestroyedRef.current = true;
 console.log(`[WebSocketProvider] Destroy debounce fired. Destroying client: ${webSocketClient.getInstanceId()}`);
 webSocketClient.destroy();
 destroyTimerRef.current = null;
 }, 150);
 };
 }, [webSocketClient]);

 useEffect(() => {
 if (loading) {
 console.log("Auth state is loading, WebSocket connection deferred.");
 return;
 }

 const debounceTimeout = setTimeout(() => {
 // Skip if client was already destroyed by the debounced cleanup
 if (isDestroyedRef.current) return;

 if (token) {
 if (!webSocketClient.isConnected() && webSocketClient.getConnectionState() !== 'connecting') {
 console.log(`[WebSocketProvider] Token available. Connecting instance: ${webSocketClient.getInstanceId()}`);
 webSocketClient.connect(token).catch(error => {
 console.error("WebSocket connection error on connect:", error);
 });
 }
 } else {
 if (webSocketClient.getConnectionState() !== 'disconnected') {
 console.log(`[WebSocketProvider] No token. Disconnecting instance: ${webSocketClient.getInstanceId()}`);
 webSocketClient.disconnect();
 }
 }
 }, 500);

 return () => clearTimeout(debounceTimeout);
 }, [token, loading, webSocketClient]);

 return (
 <WebSocketContext.Provider value={webSocketClient}>
 {children}
 </WebSocketContext.Provider>
 );
};
