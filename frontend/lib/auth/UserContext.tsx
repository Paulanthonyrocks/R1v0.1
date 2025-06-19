import React, { createContext, useContext, useState, Dispatch, SetStateAction, useEffect } from 'react';
import { UserRole } from '@/lib/auth/roles';
import { User, onAuthStateChanged, getAuth, signOut } from 'firebase/auth';
import { app } from '@/lib/firebase';

interface UserContextValue {
  userRole: UserRole;
  setUserRole: Dispatch<SetStateAction<UserRole>>;
  user: User | null;
  loading: boolean;
  logout: () => Promise<void>;
}

const UserContext = createContext<UserContextValue>({
  userRole: UserRole.VIEWER,
  setUserRole: () => {},
  user: null,
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
  const [userRole, setUserRole] = useState<UserRole>(UserRole.VIEWER);
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, async (currentUser) => {
      setUser(currentUser);
      if (currentUser) {
        try {
          const idTokenResult = await currentUser.getIdTokenResult();
          const role = idTokenResult.claims.role as UserRole | undefined;
          if (role && Object.values(UserRole).includes(role)) {
            setUserRole(role);
          } else {
            setUserRole(UserRole.VIEWER);
          }
        } catch {
          setUserRole(UserRole.VIEWER);
        }
      } else {
        setUserRole(UserRole.VIEWER);
      }
      setLoading(false);
    });
    return () => unsubscribe();
  }, []);

  const logout = async () => {
    await signOut(auth);
  };

  const value = { userRole, setUserRole, user, loading, logout };
  return <UserContext.Provider value={value}>{children}</UserContext.Provider>;
};