import React from 'react';
import RouteHistoryPanel from '../../components/RouteHistoryPanel';
import DashboardShell from '@/components/dashboard/DashboardShell';

const HistoryPage: React.FC = () => (
  <DashboardShell>
      <RouteHistoryPanel />
  </DashboardShell>
);

export default HistoryPage;