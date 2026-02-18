"use client";

import React from 'react';
import UserPreferencesPanel from '@/components/UserPreferencesPanel';
import DashboardShell from '@/components/dashboard/DashboardShell';
import AuthGuard from '@/components/auth/AuthGuard';
import { UserRole } from '@/lib/auth/roles';

const PreferencesPage: React.FC = () => (
  <AuthGuard requiredRole={UserRole.VIEWER}>
      <DashboardShell>
        <UserPreferencesPanel />
      </DashboardShell>
  </AuthGuard>
);

export default PreferencesPage;
