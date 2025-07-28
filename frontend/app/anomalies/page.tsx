"use client";
import React from 'react';
import AuthGuard from '@/components/auth/AuthGuard'; // Import AuthGuard
import { UserRole } from '@/lib/auth/roles'; // Import UserRole from correct path

const AnomaliesPage = () => {
  return (
    <AuthGuard requiredRole={UserRole.AGENCY}>
      <div>
        <h1>Anomalies Page</h1>
        <p>This is a placeholder for the Anomalies page content.</p>
      </div>
    </AuthGuard>
  );
};

export default AnomaliesPage;