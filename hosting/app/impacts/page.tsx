"use client";

import React from 'react';
import DashboardShell from '@/components/dashboard/DashboardShell';
import WeatherEventImpactPanel from '@/components/WeatherEventImpactPanel';
import AuthGuard from '@/components/auth/AuthGuard';
import { UserRole } from '@/lib/auth/roles';

const ImpactsPage: React.FC = () => (
  <AuthGuard requiredRole={UserRole.VIEWER}>
      <DashboardShell>
          <div className="retro-title-container">
              <div className="flex flex-col md:flex-row justify-between items-end gap-4">
                  <div>
                      <h1 className="text-5xl font-black uppercase tracking-tighter font-lcd matrix-glow text-lcd-text mb-1">Atmospheric Impacts</h1>
                      <div className="flex items-center gap-2">
                          <span className="terminal-text text-[10px]">ENV.MONITORING // WEATHER_EVENT_CORRELATION</span>
                      </div>
                  </div>
                  <div className="flex bg-lcd-text/5 px-4 py-2 border-2 border-lcd-text font-bold text-[10px] uppercase tracking-widest items-center gap-4">
                      <div className="flex items-center gap-2">
                          <div className="h-2 w-2 rounded-full bg-lcd-text animate-pulse" />
                          Sensor Array: Active
                      </div>
                  </div>
              </div>
          </div>
          
          <div className="max-w-6xl mx-auto w-full matrix-card p-0 overflow-hidden">
            <div className="matrix-card-header bg-lcd-text/10">
                <span>Climate Impact Analysis // Environmental Telemetry</span>
            </div>
            <div className="p-4 md:p-8 bg-lcd-text/[0.02]">
                <WeatherEventImpactPanel />
            </div>
          </div>
      </DashboardShell>
  </AuthGuard>
);

export default ImpactsPage;