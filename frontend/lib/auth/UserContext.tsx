import React, { createContext, useContext, useEffect } from 'react';
import { UserRole } from '@/lib/auth/roles';
import { User, signOut, getAuth } from 'firebase/auth';
import { app } from '@/lib/firebase';
import useAuth from '../hook/useAuth';
import { WebSocketClient } from '../websocket/WebSocketClient';

interface UserContextValue {
  userRole: UserRole;
  user: User | null;
  token: string | null;
  loading: boolean;
  logout: () => Promise<void>;
  wsClient: WebSocketClient | null;
}

const UserContext = createContext<UserContextValue>({
  userRole: UserRole.VIEWER,
  user: null,
  token: null,
  loading: true,
  logout: async () => {},
  wsClient: null,
});

export const useUser = () => {
  return useContext(UserContext);
};

interface UserProviderProps {
  children: React.ReactNode;
}

const auth = getAuth(app);

export const UserProvider: React.FC<UserProviderProps> = ({ children }) => {
  const { user, token, wsClient, loading } = useAuth();
  const [userRole, setUserRole] = React.useState<UserRole>(UserRole.VIEWER);

  useEffect(() => {
    let unsubscribe: (() => void) | undefined;
    
    if (user) {
      // Set up token refresh listener
      unsubscribe = auth.onIdTokenChanged(async (user) => {
        if (user) {
          const idTokenResult = await user.getIdTokenResult();
          const role = idTokenResult.claims.role as UserRole | undefined;
          if (role && Object.values(UserRole).includes(role)) {
            setUserRole(role);
          } else {
            setUserRole(UserRole.VIEWER);
          }
          
          // If we have a WebSocket client, update its token
          if (wsClient) {
            const newToken = await user.getIdToken();
            await wsClient.reconnectWithNewToken(newToken);
          }
        }
      });
    } else {
      setUserRole(UserRole.VIEWER);
    }

    return () => {
      if (unsubscribe) {
        unsubscribe();
      }
    };
  }, [user, wsClient]); // Add wsClient to dependencies

  const logout = async () => {
    await signOut(auth);
  };

  return (
    <UserContext.Provider value={{ userRole, user, token, loading, logout, wsClient }}>
      {children}
    </UserContext.Provider>
  );
};