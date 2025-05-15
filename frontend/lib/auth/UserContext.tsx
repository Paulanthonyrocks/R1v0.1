import React, { createContext, useContext, useState, Dispatch, SetStateAction, useEffect } from 'react';
import { UserRole } from '@/lib/auth/roles';
import { User, onAuthStateChanged, getAuth } from 'firebase/auth';
import { app } from '@/lib/firebase';

interface UserContextValue {
  userRole: UserRole;
  setUserRole: Dispatch<SetStateAction<UserRole>>;
  user: User | null;
  loading: boolean;
}

const UserContext = createContext<UserContextValue>({
  userRole: UserRole.VIEWER,
  setUserRole: () => {},
  user: null,
  loading: true,
});

export const useUser = () => {
  return useContext(UserContext);
};

interface UserProviderProps {
  children: React.ReactNode;
}

const auth = getAuth(app);

export const UserProvider: React.FC<UserProviderProps> = ({ children }) => {
  const [userRole, setUserRole] = useState<UserRole>(UserRole.VIEWER);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, (currentUser) => {
      setUser(currentUser);
      setLoading(false);
    });
    return () => unsubscribe();
  }, []);

  const value = { userRole, setUserRole, user, loading };
  return <UserContext.Provider value={value}>{children}</UserContext.Provider>;
};