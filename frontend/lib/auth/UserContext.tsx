import React, { createContext, useContext, useState, Dispatch, SetStateAction, useEffect } from 'react';
import { UserRole } from '@/lib/auth/roles';
import { User, onAuthStateChanged, getAuth } from 'firebase/auth';
import { app } from '@/lib/firebase'; // Import the initialized Firebase app

interface UserContextValue {
  userRole: UserRole;
  setUserRole: Dispatch<SetStateAction<UserRole>>;
  user: User | null; // Add the user property
  loading: boolean; // Add loading property
}

const UserContext = createContext<UserContextValue>({ // Update initial value
  userRole: UserRole.VIEWER,
  setUserRole: () => {},
  user: null, // Add the user property initialized to null
  loading: true, // Initialize loading to true
});

export const useUser = () => {
  return useContext(UserContext);
};

interface UserProviderProps {
    children: React.ReactNode;
}

const auth = getAuth(app); // Get the auth instance using the initialized app

export const UserProvider: React.FC<UserProviderProps> = ({ children }) => {
  const [userRole, setUserRole] = useState<UserRole>(UserRole.VIEWER);
  const [user, setUser] = useState<User | null>(null); // State to hold the authenticated user
  const [loading, setLoading] = useState(true); // Add loading state

  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, (currentUser) => {
      setUser(currentUser);
      setLoading(false); // Set loading to false after state is determined
    });
    return () => unsubscribe(); // Clean up the listener
  }, []);

  const value = { userRole, setUserRole, user, loading }; // Include loading in the context value
  return <UserContext.Provider value={value}>{children}</UserContext.Provider>;
};