// frontend/app/dashboard/page.tsx
"use client";

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { APIResponse, AllNodesCongestionResponse } from '@/lib/types/api';
import AuthGuard from "@/components/auth/AuthGuard";
import { Signal, BatteryFull } from 'lucide-react';
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { UserRole } from "@/lib/auth/roles";
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';

import { getToken } from '@/lib/auth/getToken';
import AnomalyItem from '@/components/dashboard/AnomalyItem';
import StatCard from '@/components/dashboard/StatCard';
import {
  Activity, Zap, AlertTriangle, Users, TrendingDown, TrendingUp, CheckCircle2, ShieldCheck
} from 'lucide-react';
import SurveillanceFeed from '@/components/dashboard/SurveillanceFeed';
import { BackendCongestionNodeData, FeedStatusData } from '@/lib/types';

const DashboardPage: React.FC = () => {
  const { kpis, alerts, feeds, isConnected, isReady, error, subscribeToFeed } = useRealtimeUpdates();

  // Log feeds from the hook for debugging
  console.log("DashboardPage - feeds from useRealtimeUpdates:", feeds);

  // Find the first sample feed to display
  const sampleFeed = feeds.length > 0 ? feeds[0] : null;

  // Log sampleFeed for debugging
  console.log("DashboardPage - sampleFeed:", sampleFeed);

  // Effect to log when feeds actually update in DashboardPage and subscribe
  useEffect(() => {
    console.log("DashboardPage useEffect - feeds updated:", feeds);
    if (feeds.length > 0 && isConnected && isReady) {
      console.log("DashboardPage: Feeds available, subscribing to first feed:", feeds[0].feed_id);
      subscribeToFeed(feeds[0].feed_id);
    }
  }, [feeds, isConnected, isReady, subscribeToFeed]);

  // Helper functions to determine status icons
  const getCongestionStatusIcon = (val: number | undefined) => {
    if (val === undefined || val === null) return undefined;
    if (val > 70) return TrendingDown;
    if (val > 40) return TrendingUp;
    return CheckCircle2;
  };

  const getSpeedStatusIcon = (val: number | undefined) => {
    if (val === undefined || val === null) return undefined;
    if (val < 20) return TrendingDown;
    if (val < 40) return TrendingUp;
    return CheckCircle2;
  };

  const getIncidentStatusIcon = (val: number | undefined) => {
    if (val === undefined || val === null) return undefined;
    if (val > 0) return AlertTriangle;
    return ShieldCheck;
  };

  // Type guard for location object
  function isLatLng(obj: unknown): obj is { latitude: number; longitude: number } {
    return (
      typeof obj === 'object' &&
      obj !== null &&
      typeof (obj as { latitude?: unknown }).latitude === 'number' &&
      typeof (obj as { longitude?: unknown }).longitude === 'number'
    );
  }

  const congestionIndex = null;
  const activeIncidents = null;
  const averageSpeed = kpis?.avg_speed;
  const totalFlow = kpis?.vehicle_count;

  return (
    <AuthGuard requiredRole={UserRole.VIEWER}>
      <div className="bg-lcd-text text-lcd-bg font-lcd flex flex-col min-h-screen w-full">
        {/* Dashboard Status Bar */}
        <header className="bg-lcd-bg text-lcd-text font-lcd flex items-center justify-between px-4 py-1 border-b-2 border-lcd-text">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <div className="flex items-center space-x-2 cursor-pointer">
                <Signal size={20} />
                <span className="font-lcd matrix-glow">DASHBOARD</span>
              </div>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="matrix-card">
              <DropdownMenuItem asChild>
                <Link href="/" className="w-full tracking-normal font-lcd matrix-glow">HOME</Link>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <Link href="/preferences" className="w-full tracking-normal font-lcd matrix-glow">PREFERENCES</Link>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <Link href="/history" className="w-full tracking-normal font-lcd matrix-glow">ROUTE HISTORY</Link>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="/impacts" className="w-full tracking-normal font-lcd matrix-glow">WEATHER & EVENTS</a>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <Link href="/surveillance" className="w-full tracking-normal font-lcd matrix-glow">SURVEILLANCE</Link>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <div className="flex items-center space-x-2">
            <span className="font-lcd matrix-glow">{new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
            <BatteryFull size={20} />
          </div>
        </header>

        <main className="flex-1 p-4">
          <h1 className="text-2xl font-bold mb-4 uppercase tracking-widest text-center font-lcd matrix-glow text-lcd-bg">DASHBOARD OVERVIEW</h1>

          {/* Real-time Analytics Cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4 mb-6 matrix-card">
            <StatCard
              title="Congestion Index"
              value={`${congestionIndex ?? '--'}%`}
              icon={Activity}
              statusIcon={getCongestionStatusIcon(typeof congestionIndex === 'number' ? congestionIndex : undefined)}
              change="N/A"
              changeText="Change data not available"
            />
            <StatCard
              title="Average Speed"
              value={`${averageSpeed ? averageSpeed.toFixed(2) : '--'} km/h`}
              icon={Zap}
              statusIcon={getSpeedStatusIcon(typeof averageSpeed === 'number' ? averageSpeed : undefined)}
              change="N/A"
              changeText="Change data not available"
            />
            <StatCard
              title="Active Incidents"
              value={`${activeIncidents ?? '--'}`}
              icon={AlertTriangle}
              statusIcon={getIncidentStatusIcon(typeof activeIncidents === 'number' ? activeIncidents : undefined)}
              change="N/A"
              changeText="Change data not available"
            />
            <StatCard
              title="Total Flow"
              value={`${totalFlow ?? '--'} vehicles/hr`}
              icon={Users}
              change="N/A"
              changeText="Change data not available"
            />
          </div>

          {/* Sample Video Feed */}
          <div className="mb-4 matrix-card p-4">
            <h2 className="text-xl font-semibold mb-2 tracking-normal font-lcd matrix-glow text-lcd-text group-hover:text-lcd-bg">SAMPLE VIDEO FEED</h2>
            
            {/* WebSocket Connection Status */}
            <div className="mb-3 p-2 matrix-card">
              <div className="flex items-center gap-2 text-sm">
                <div className={`w-3 h-3 rounded-full ${isConnected ? 'bg-green-500' : 'bg-red-500'}`}></div>
                <span className="font-lcd matrix-glow text-lcd-text">
                  WebSocket: {isConnected ? 'CONNECTED' : 'DISCONNECTED'}
                  {isReady && ' (READY)'}
                  {error && ` - ERROR: ${error}`}
                
                </span>
              </div>
              <div className="text-xs text-lcd-text mt-1">
                Feeds Available: {feeds.length} | Last Update: {new Date().toLocaleTimeString()}
              </div>
            </div>
            
            <div className="w-full overflow-x-auto flex gap-4 p-2 matrix-card whitespace-nowrap">
              {isReady && sampleFeed ? (
                <div className="inline-block min-w-[320px] max-w-[480px] w-full align-top">
                  <SurveillanceFeed feed={sampleFeed} />
                </div>
              ) : (
                <p className="text-lcd-text group-hover:text-lcd-bg tracking-normal font-lcd matrix-glow">
                  {!isConnected ? 'CONNECTING TO BACKEND...' : 
                   !isReady ? 'ESTABLISHING CONNECTION...' : 
                   feeds.length === 0 ? 'NO FEEDS AVAILABLE' : 'LOADING SAMPLE FEED...'}
                </p>
              )}
            </div>
          </div>

          {/* Live Alerts Section */}
          <div className="mb-6 matrix-card p-4">
            <h2 className="text-xl font-semibold mb-3 tracking-normal font-lcd matrix-glow text-lcd-text group-hover:text-lcd-bg">LIVE ALERTS</h2>
            {!isReady && <p className="text-lcd-text group-hover:text-lcd-bg tracking-normal font-lcd matrix-glow">CONNECTING TO LIVE ALERTS...</p>}
            {isReady && alerts.length === 0 && <p className="text-lcd-text group-hover:text-lcd-bg tracking-normal font-lcd matrix-glow">NO NEW ALERTS.</p>}
            {isReady && alerts.length > 0 && (
              <div className="space-y-3 max-h-96 overflow-y-auto matrix-card">
                {alerts.slice(-10).reverse().map((alert) => (
                  <AnomalyItem
                    key={alert.id || new Date(alert.timestamp).toISOString()}
                    timestamp={new Date(alert.timestamp).toLocaleString()}
                    severity={alert.severity || 'info'}
                    message={alert.message}
                    location={
                      alert.details && typeof alert.details === 'object' && isLatLng(alert.details.location)
                        ? `Lat: ${alert.details.location.latitude}, Lon: ${alert.details.location.longitude}`
                        : 'N/A'
                    }
                    details={typeof alert.details === 'object' ? alert.details : undefined}
                  />
                ))}
              </div>
            )}
          </div>
        </main>
      </div>
    </AuthGuard>
  );
};

export default DashboardPage;