import { useEffect, useState, useRef } from 'react';
import { auth } from '../firebase'; // Adjust this path to your Firebase auth instance
import { onAuthStateChanged, User } from 'firebase/auth';
import { WebSocketClient } from '../websocket'; // Adjust this path to your WebSocketClient class

const useAuth = () => {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const wsClientRef = useRef<WebSocketClient | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!auth) return;
    const unsubscribe = onAuthStateChanged(auth, async (user) => {
      setLoading(true);
      if (user) {
        setUser(user);
        const idToken = await user.getIdToken();
        setToken(idToken);

        // Disconnect existing client if any
        if (wsClientRef.current) {
          wsClientRef.current.disconnect();
        }

        // Initialize WebSocket client with token
        const clientId = crypto.randomUUID();
        const wsBase = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000';
        const wsUrl = `${wsBase.replace(/\/$/, '')}/ws/${clientId}`;
        const client = new WebSocketClient(wsUrl, idToken);
        wsClientRef.current = client;
        
        client.connect().catch(err => console.error("WebSocket connection error:", err));

      } else {
        setUser(null);
        setToken(null);
        if (wsClientRef.current) {
          wsClientRef.current.disconnect();
        }
        wsClientRef.current = null;
      }
      setLoading(false);
    });

    return () => {
      unsubscribe();
      if (wsClientRef.current) {
        wsClientRef.current.disconnect();
      }
    };
  }, []);

  return { user, token, wsClient: wsClientRef.current, loading };
};

export default useAuth;