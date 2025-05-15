"use client";

import React, { useEffect } from 'react';
import { useUser } from '@/lib/auth/UserContext'; // Adjust the import path if necessary
import { useRouter, usePathname } from 'next/navigation';
import { UserRole } from '@/lib/auth/roles';

// Define props including the optional requiredRole
const AuthGuard: React.FC<{ children: React.ReactNode, requiredRole?: UserRole }> = ({ children, requiredRole }) => {
  const { user, loading, userRole } = useUser(); // Get userRole from context
  const pathname = usePathname();
  const router = useRouter();

  useEffect(() => {
    if (!loading) {
      if (!user) {
        // Redirect to the login page if not authenticated
        router.push('/login');
      } else if (requiredRole && userRole !== requiredRole) {
        // Redirect to unauthorized page if authenticated but role doesn't match
        // You might want a dedicated unauthorized page or handle this differently
        console.warn(`User with role ${userRole} attempted to access content requiring role ${requiredRole}`);
        router.push('/unauthorized'); // Assuming an /unauthorized route exists
      }
    }
  }, [user, loading, userRole, requiredRole, router, pathname]); // Add userRole and requiredRole to dependencies

  // Render a loading indicator while authentication state is being determined
  if (loading) {
    return <div>Loading...</div>; // Replace with a proper loading component/spinner
  }

  // If authenticated and role matches (or no requiredRole), render children
  if (user && (!requiredRole || userRole === requiredRole)) {
    return <>{children}</>;
  }

  // If not authenticated or role doesn't match, useEffect handles redirect, render nothing
  return null;
};

export default AuthGuard;