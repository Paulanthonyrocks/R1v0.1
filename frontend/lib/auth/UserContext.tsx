import React, { createContext, useContext, useState, Dispatch, SetStateAction } from 'react';
import { UserRole } from '@/lib/auth/roles';

interface UserContextValue {
  userRole: UserRole;
  setUserRole: Dispatch<SetStateAction<UserRole>>;
}

const UserContext = createContext<UserContextValue>({
  userRole: UserRole.VIEWER,
  setUserRole: () => {},
});

export const useUser = () => {
  return useContext(UserContext);
};

interface UserProviderProps {
    children: React.ReactNode;
}

export const UserProvider: React.FC<UserProviderProps> = ({ children }) => {
  const [userRole, setUserRole] = useState<UserRole>(UserRole.VIEWER);
  const value = { userRole, setUserRole };
  return <UserContext.Provider value={value}>{children}</UserContext.Provider>;
};