'use client';

import { useEffect, useState, createContext, useContext, ReactNode } from 'react';
import { auth } from '@/lib/firebase';
import { onAuthStateChanged, User } from 'firebase/auth';
import { TokenManager } from '@/lib/auth/TokenManager';
import { UserRole } from '@/lib/auth/roles';

interface AuthContextValue {
  user: User | null;
  token: string | null;
  userRole: UserRole;
  loading: boolean;
}

const AuthContext = createContext<AuthContextValue> ({
  user: null,
  token: null,
  userRole: UserRole.VIEWER,
  loading: true,
});

export const useAuth = () => useContext(AuthContext);

export const AuthProvider = ({ children }: { children: ReactNode }) => {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [userRole, setUserRole] = useState<UserRole>(UserRole.VIEWER);
  const [loading, setLoading] = useState(true);
  const tokenManager = TokenManager.getInstance();

  useEffect(() => {
    if (!auth) {
      // eslint-disable-next-line react-hooks/set-state-in-effect -- one-time auth-unavailable fast path on mount; no external subscription to attach to
      setLoading(false);
      return;
    }

    const unsubscribe = onAuthStateChanged(auth, async (user) => {
      if (user) {
        setUser(user);
        const idTokenResult = await user.getIdTokenResult();
        const role = idTokenResult.claims.role as UserRole | undefined;
        if (role && Object.values(UserRole).includes(role)) {
          setUserRole(role);
        } else {
          setUserRole(UserRole.VIEWER);
        }
        await tokenManager.updateToken(user);
        setToken(tokenManager.getCurrentToken());
      } else {
        setUser(null);
        setToken(null);
        setUserRole(UserRole.VIEWER);
        await tokenManager.updateToken(null);
      }
      setLoading(false);
    });

    const unsubscribeTokenRefresh = tokenManager.onTokenRefresh((newToken) => {
      setToken(newToken);
    });

    return () => {
      unsubscribe();
      unsubscribeTokenRefresh();
    };
  }, [tokenManager]);

  return (
    <AuthContext.Provider value={{ user, token, userRole, loading }}>
      {children}
    </AuthContext.Provider>
  );
};