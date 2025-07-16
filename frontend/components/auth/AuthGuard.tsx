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
    if (!loading) {
      if (!user) {
        // Store the current path before redirecting
        const currentPath = pathname ?? '/';
        sessionStorage.setItem('redirectPath', currentPath);
        // Redirect to the login page if not authenticated
        router.push('/login');
      } else if (requiredRole) {
        // Add a small delay to allow for token refresh
        const timer = setTimeout(() => {
          // Check if user has required role or is admin
          const hasAccess = userRole === requiredRole || userRole === UserRole.ADMIN;
          if (!hasAccess) {
            console.warn(`User with role ${userRole} attempted to access content requiring role ${requiredRole}`);
            router.push('/unauthorized');
          }
        }, 1000);
        return () => clearTimeout(timer);
      }
    }
  }, [user, loading, userRole, requiredRole, router, pathname]); // Add userRole and requiredRole to dependencies

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