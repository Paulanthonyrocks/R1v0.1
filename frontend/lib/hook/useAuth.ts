import { useEffect, useState, useRef } from 'react';
import { auth } from '../firebase'; // Adjust this path to your Firebase auth instance
import { onAuthStateChanged, User } from 'firebase/auth';
import { WebSocketClient } from '../websocket/WebSocketClient';
import { TokenManager } from '../auth/TokenManager'; // Import TokenManager

const getOrCreateClientId = () => {
    if (typeof window === 'undefined') return '';
    let clientId = localStorage.getItem('ws_client_id');
    if (!clientId) {
        clientId = crypto.randomUUID();
        localStorage.setItem('ws_client_id', clientId);
    }
    return clientId;
};

const useAuth = () => {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const wsClientRef = useRef<WebSocketClient | null>(null);
  const [loading, setLoading] = useState(true);
  const tokenManager = TokenManager.getInstance(); // Get TokenManager instance

  useEffect(() => {
    if (!auth) return;

    // Subscribe to token refresh events from TokenManager
    const unsubscribeTokenRefresh = tokenManager.onTokenRefresh((newToken) => {
      setToken(newToken);
      if (wsClientRef.current) {
        wsClientRef.current.reconnectWithNewToken(newToken);
      }
    });

    const unsubscribe = onAuthStateChanged(auth, async (user) => {
      setLoading(true);
      if (user) {
        setUser(user);
        await tokenManager.updateToken(user); // Update TokenManager with the current user
        setToken(tokenManager.getCurrentToken()); // Set the token state

        // Disconnect existing client if any
        if (wsClientRef.current) {
          wsClientRef.current.disconnect();
        }

        // Initialize WebSocket client with token
        const wsBase = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000';
        const wsUrl = `${wsBase.replace(/\/$/, '')}/ws/${getOrCreateClientId()}`;
        const client = new WebSocketClient(wsUrl);
        wsClientRef.current = client;

      } else {
        setUser(null);
        setToken(null); // Clear token when user logs out
        await tokenManager.updateToken(null); // Clear token in TokenManager
        if (wsClientRef.current) {
          wsClientRef.current.disconnect();
        }
        wsClientRef.current = null;
      }
      setLoading(false);
    });

    return () => {
      unsubscribe();
      unsubscribeTokenRefresh(); // Clean up token refresh subscription
      if (wsClientRef.current) {
        wsClientRef.current.disconnect();
      }
    };
  }, []);

  return { user, token, wsClient: wsClientRef.current, loading };
};

export default useAuth;