"use client";

import React from 'react';
import dynamic from 'next/dynamic';
import AuthGuard from "@/components/auth/AuthGuard";
import { UserRole } from "@/lib/auth/roles";
import DashboardShell from '@/components/dashboard/DashboardShell';

// Dynamically import LeafletMap to ensure it's client-side rendered (Leaflet needs 'window')
const DynamicLeafletMap = dynamic(() => import('@/components/map/LeafletMap'), {
  ssr: false,
  loading: () => (
    <div className="fixed inset-0 bg-[#0a0a0a] text-[#00ff41] font-mono flex items-center justify-center z-50">
      <div className="animate-pulse text-2xl tracking-normal">INITIALIZING NEURAL MAP...</div>
    </div>
  )
});

const LiveMapPage: React.FC = () => {
  return (
    <AuthGuard requiredRole={UserRole.VIEWER}>
      <DashboardShell className="p-0 md:p-0 max-w-none h-screen overflow-hidden">
        <div className="fixed top-[64px] left-0 right-0 bottom-0 z-0 bg-[#0a0a0a]">
          <div className="absolute top-4 left-0 right-0 z-[1001] pointer-events-none">
             <h1 className="text-xl font-bold uppercase tracking-[0.3em] text-center font-mono text-[#00ff41] drop-shadow-[0_0_8px_rgba(0,255,65,0.5)]">
               Live Surveillance Grid
             </h1>
          </div>
          <div className="w-full h-full">
            <DynamicLeafletMap />
          </div>
        </div>
      </DashboardShell>
    </AuthGuard>
  );
};

export default LiveMapPage;