"use client";

import React, { useEffect } from 'react';
import { useUser } from '@/lib/auth/UserContext';
import { useRouter, usePathname } from 'next/navigation';
import { UserRole } from '@/lib/auth/roles';

interface AuthGuardProps {
  children: React.ReactNode;
  requiredRole?: UserRole;
}

// Role hierarchy, lowest privilege first. A user at a given level may access
// anything requiring their level or below (ADMIN bypasses everything).
const ROLE_RANK: Record<UserRole, number> = {
  [UserRole.VIEWER]: 1,
  [UserRole.PLANNER]: 2,
  [UserRole.OPERATOR]: 3,
  [UserRole.AGENCY]: 4,
  [UserRole.DRIVER]: 2,
  [UserRole.ADMIN]: 99,
};

const hasAccess = (userRole: UserRole, requiredRole?: UserRole): boolean => {
  if (!requiredRole) return true;
  if (userRole === UserRole.ADMIN) return true;
  return (ROLE_RANK[userRole] ?? 0) >= (ROLE_RANK[requiredRole] ?? 0);
};

const AuthGuard: React.FC<AuthGuardProps> = ({ children, requiredRole }) => {
  const { user, loading, userRole } = useUser();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {

    if (loading) {
      // Still loading user data, do nothing yet
      return;
    }

    if (!user) {
      // User is not authenticated, redirect to login
      console.log('AuthGuard: User not authenticated, redirecting to login.');
      const currentPath = pathname ?? '/';
      sessionStorage.setItem('redirectPath', currentPath);
      router.push('/login');
      return;
    }

    // User is authenticated, check roles if required (hierarchy: higher roles
    // inherit access to lower-role pages, ADMIN bypasses everything)
    if (requiredRole && !hasAccess(userRole, requiredRole)) {
      console.warn(`AuthGuard: User with role ${userRole} attempted to access content requiring role ${requiredRole}. Redirecting to unauthorized.`);
      router.push('/unauthorized');
    }
  }, [user, loading, userRole, requiredRole, router, pathname]);

  // Render a loading indicator while authentication state is being determined
  if (loading) {
    return <div>Loading...</div>; // Replace with a proper loading component/spinner
  }

  // If authenticated and (no required role or has access), render children
  if (user && hasAccess(userRole, requiredRole)) {
    return <>{children}</>;
  }

  // If not authenticated or role doesn't match, useEffect handles redirect, render nothing
  return null;
};

export default AuthGuard;