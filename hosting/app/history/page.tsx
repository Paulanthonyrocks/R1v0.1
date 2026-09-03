"use client";

import React from 'react';
import AuthGuard from '@/components/auth/AuthGuard';
import RouteHistoryPanel from '../../components/RouteHistoryPanel';
import DashboardShell from '@/components/dashboard/DashboardShell';

const HistoryPage: React.FC = () => (
  <AuthGuard>
      <DashboardShell>
          <RouteHistoryPanel />
      </DashboardShell>
  </AuthGuard>
);

export default HistoryPage;
