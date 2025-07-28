"use client";

import React, { useEffect } from 'react';
import { useUser } from '@/lib/auth/UserContext';
import { useRouter, usePathname } from 'next/navigation';
import { UserRole } from '@/lib/auth/roles';

interface AuthGuardProps {
  children: React.ReactNode;
  requiredRole?: UserRole;
}

const AuthGuard: React.FC<AuthGuardProps> = ({ children, requiredRole }) => {
  const { user, loading, userRole } = useUser();
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    console.log('AuthGuard: user, loading, userRole changed', { user, loading, userRole, requiredRole, pathname });

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

    // User is authenticated, check roles if required
    if (requiredRole) {
      const hasAccess = userRole === requiredRole || userRole === UserRole.ADMIN;
      if (!hasAccess) {
        console.warn(`AuthGuard: User with role ${userRole} attempted to access content requiring role ${requiredRole}. Redirecting to unauthorized.`);
        router.push('/unauthorized');
      }
    }
  }, [user, loading, userRole, requiredRole, router, pathname]);

  // Render a loading indicator while authentication state is being determined
  if (loading) {
    return <div>Loading...</div>; // Replace with a proper loading component/spinner
  }

  // If authenticated and (no required role or has access), render children
  if (user && (!requiredRole || userRole === requiredRole || userRole === UserRole.ADMIN)) {
    return <>{children}</>;
  }

  // If not authenticated or role doesn't match, useEffect handles redirect, render nothing
  return null;
};

export default AuthGuard;