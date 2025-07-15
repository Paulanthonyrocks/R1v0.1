import React, { createContext, useContext, useEffect } from 'react';
import { UserRole } from '@/lib/auth/roles';
import { User, signOut, getAuth } from 'firebase/auth';
import { app } from '@/lib/firebase';
import useAuth from '../hook/useAuth'; // Import the useAuth hook
import { WebSocketClient } from '../websocket';

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
    if (user) {
      user.getIdTokenResult().then(idTokenResult => {
        const role = idTokenResult.claims.role as UserRole | undefined;
        if (role && Object.values(UserRole).includes(role)) {
          setUserRole(role);
        } else {
          setUserRole(UserRole.VIEWER);
        }
      });
    } else {
      setUserRole(UserRole.VIEWER);
    }
  }, [user]);

  const logout = async () => {
    await signOut(auth);
  };

  return (
    <UserContext.Provider value={{ userRole, user, token, loading, logout, wsClient }}>
      {children}
    </UserContext.Provider>
  );
};