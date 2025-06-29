"use client";

import React, { useState, useEffect } from 'react';
import dynamic from 'next/dynamic';
import AuthGuard from "@/components/auth/AuthGuard";
import { UserRole } from "@/lib/auth/roles";
import { Signal, BatteryFull, Clock } from 'lucide-react';
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from "@/components/ui/dropdown-menu";

// Dynamically import CesiumGlobe to ensure it's client-side rendered
const DynamicCesiumGlobe = dynamic(() => import('@/components/CesiumGlobe'), {
  ssr: false,
  loading: () => (
    <div className="fixed inset-0 bg-lcd-bg text-lcd-text font-lcd flex items-center justify-center z-50">
      <div className="animate-pulse text-2xl tracking-normal">LOADING GLOBE...</div>
    </div>
  ),
});

const LiveMapPage: React.FC = () => {
  const [time, setTime] = useState("--:--");

  useEffect(() => {
    const timer = setInterval(() => {
      setTime(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  return (
    <AuthGuard requiredRole={UserRole.PLANNER}>
      <div className="bg-lcd-bg text-lcd-text font-lcd flex flex-col min-h-screen w-full">
        {/* Status Bar */}
        <header className="flex items-center justify-between px-4 py-1 border-b-2 border-lcd-text">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <div className="flex items-center space-x-2 cursor-pointer">
                <Signal size={20} />
                <span className="font-lcd matrix-glow">LIVE MAP</span>
              </div>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="matrix-card">
              <DropdownMenuItem asChild>
                <a href="/" className="w-full tracking-normal font-lcd matrix-glow">HOME</a>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="/dashboard" className="w-full tracking-normal font-lcd matrix-glow">DASHBOARD</a>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="/anomalies" className="w-full tracking-normal font-lcd matrix-glow">ANOMALIES</a>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="/dashboard/logs" className="w-full tracking-normal font-lcd matrix-glow">SYSTEM LOGS</a>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="/dashboard/analytics" className="w-full tracking-normal font-lcd matrix-glow">ANALYTICS</a>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="/dashboard/preferences" className="w-full tracking-normal font-lcd matrix-glow">PREFERENCES</a>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <div className="flex items-center space-x-2">
            <Clock size={20} />
            <span className="font-lcd matrix-glow">{time}</span>
            <BatteryFull size={20} />
          </div>
        </header>

        <main className="flex-1 flex flex-col items-center justify-center p-4">
          <h1 className="text-2xl font-bold mb-4 uppercase tracking-widest text-center font-lcd matrix-glow">GLOBAL TRAFFIC OVERVIEW</h1>
          <div className="w-full h-[calc(100vh-200px)]">
            <DynamicCesiumGlobe />
          </div>
        </main>
      </div>
    </AuthGuard>
  );
};

export default LiveMapPage;
