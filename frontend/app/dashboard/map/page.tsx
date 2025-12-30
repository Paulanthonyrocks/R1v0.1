"use client";

import React from 'react';
import dynamic from 'next/dynamic';
import AuthGuard from "@/components/auth/AuthGuard";
import { UserRole } from "@/lib/auth/roles";
import DashboardShell from '@/components/dashboard/DashboardShell';

// Dynamically import CesiumGlobe to ensure it's client-side rendered
const DynamicCesiumGlobe = dynamic(() => import('@/components/CesiumGlobe'), {
  ssr: false,
  loading: () => (
    <div className="fixed inset-0 bg-lcd-text text-lcd-bg font-lcd flex items-center justify-center z-50">
      <div className="animate-pulse text-2xl tracking-normal matrix-glow">LOADING GLOBE...</div>
    </div>
  ),
});

const LiveMapPage: React.FC = () => {
  return (
    <AuthGuard requiredRole={UserRole.PLANNER}>
      <DashboardShell className="flex flex-col h-[calc(100vh-64px)] p-0 md:p-0 max-w-none">
        <div className="flex-1 flex flex-col relative">
          <div className="absolute top-4 left-0 right-0 z-10 pointer-events-none">
             <h1 className="text-2xl font-bold uppercase tracking-widest text-center font-lcd matrix-glow text-lcd-bg drop-shadow-md">GLOBAL TRAFFIC OVERVIEW</h1>
          </div>
          <div className="flex-1 w-full h-full">
            <DynamicCesiumGlobe />
          </div>
        </div>
      </DashboardShell>
    </AuthGuard>
  );
};

export default LiveMapPage;