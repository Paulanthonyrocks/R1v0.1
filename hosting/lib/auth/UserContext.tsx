import React, { createContext, useContext } from 'react';
import { UserRole } from '@/lib/auth/roles';
import { User, signOut, getAuth } from 'firebase/auth';
import { app } from '@/lib/firebase';
import useAuth from '../hook/useAuth';

interface UserContextValue {
  userRole: UserRole;
  user: User | null;
  token: string | null;
  loading: boolean;
  logout: () => Promise<void>;
}

const UserContext = createContext<UserContextValue>({
  userRole: UserRole.VIEWER,
  user: null,
  token: null,
  loading: true,
  logout: async () => {},
});

export const useUser = () => {
  return useContext(UserContext);
};

interface UserProviderProps {
  children: React.ReactNode;
}

const auth = getAuth(app);

export const UserProvider: React.FC<UserProviderProps> = ({ children }) => {
  const { user, token, userRole, loading } = useAuth();

  const logout = async () => {
    await signOut(auth);
  };

  return (
    <UserContext.Provider value={{ userRole, user, token, loading, logout }}>
      {children}
    </UserContext.Provider>
  );
};