import { useEffect, useState } from 'react';
import { auth } from '../firebase'; // Adjust this path to your Firebase auth instance
import { onAuthStateChanged, User } from 'firebase/auth';
import { TokenManager } from '../auth/TokenManager'; // Import TokenManager
import { UserRole } from '../auth/roles';

const useAuth = () => {
  const [user, setUser] = useState<User | null>(null);
  const [token, setToken] = useState<string | null>(null);
  const [userRole, setUserRole] = useState<UserRole>(UserRole.VIEWER);
  const [loading, setLoading] = useState(true);
  const tokenManager = TokenManager.getInstance(); // Get TokenManager instance

  useEffect(() => {
    if (!auth) {
      setLoading(false);
      return;
    }

    // This listener handles user sign-in and sign-out
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
        // The TokenManager will now handle getting the token and refreshing it.
        await tokenManager.updateToken(user);
        setToken(tokenManager.getCurrentToken());
      } else {
        setUser(null);
        setToken(null); // Clear token when user logs out
        setUserRole(UserRole.VIEWER);
        await tokenManager.updateToken(null); // Clear token in TokenManager
      }
      setLoading(false);
    });

    // This listener handles token refreshes for an existing user
    const unsubscribeTokenRefresh = tokenManager.onTokenRefresh((newToken) => {
      setToken(newToken);
    });

    return () => {
      unsubscribe();
      unsubscribeTokenRefresh(); // Clean up token refresh subscription
    };
  }, [tokenManager]);

  return { user, token, userRole, loading };
};

export default useAuth;