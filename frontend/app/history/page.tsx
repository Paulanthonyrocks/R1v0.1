import React from 'react';
import RouteHistoryPanel from '../../components/RouteHistoryPanel';

const HistoryPage: React.FC = () => (
  // Changed bg-gray-50 to bg-background, added tracking-normal
  <main className="min-h-screen bg-background p-8 tracking-normal">
    <RouteHistoryPanel />
  </main>
);

export default HistoryPage;
