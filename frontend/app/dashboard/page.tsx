// frontend/app/dashboard/page.tsx
"use client";

import React, { useEffect, useState } from 'react';
import Link from 'next/link';
import { APIResponse, AllNodesCongestionResponse } from '@/lib/types/api';
import AuthGuard from "@/components/auth/AuthGuard"; // Import AuthGuard
import { Signal, BatteryFull } from 'lucide-react'; // Import Signal and BatteryFull icons
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from "@/components/ui/dropdown-menu"; // Import DropdownMenu components
import { UserRole } from "@/lib/auth/roles"; // Import UserRole
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates'; // Import the hook

import { getToken } from '@/lib/auth/getToken'; // Import getToken
import AnomalyItem from '@/components/dashboard/AnomalyItem'; // Import AnomalyItem
import StatCard from '@/components/dashboard/StatCard'; // Import StatCard
import {
  Activity, Zap, AlertTriangle, Users, TrendingDown, TrendingUp, CheckCircle2, ShieldCheck
} from 'lucide-react'; // Import Lucide icons & new status icons
import SurveillanceFeed from '@/components/dashboard/SurveillanceFeed'; // Import SurveillanceFeed
import { BackendCongestionNodeData } from '@/lib/types';

const DashboardPage: React.FC = () => {
  // State to hold WebSocket messages (optional, for display/debugging) - can be removed or adapted
  const [debugMessages, setDebugMessages] = useState<string[]>([]);

  // Use the realtime updates hook - This is now the primary source for KPIs and feeds
  
  const { /* kpis, */ alerts, feeds, isConnected, isReady, error } = useRealtimeUpdates();

  // Find the first sample feed to display. In a real app, you might have a more robust selection logic.
  const sampleFeed = feeds.length > 0 ? feeds[0] : null;
  console.log("Sample Feed:", sampleFeed);
  console.log("WebSocket Status - Connected:", isConnected, "Ready:", isReady, "Error:", error);
  console.log("Feeds Array Length:", feeds.length);
  console.log("All Feeds:", feeds);

  // State for REST API congestion index
  const [congestionIndex, setCongestionIndex] = useState<number | null>(null);

  // State for REST API KPIs
  const [averageSpeed, setAverageSpeed] = useState<number | null>(null);
  const [activeIncidents, setActiveIncidents] = useState<number | null>(null); // Placeholder, see note below
  const [totalFlow, setTotalFlow] = useState<number | null>(null);

  // The useRealtimeUpdates hook now manages its own connection lifecycle.
  // The useEffect that previously called startWebSocket() has been removed.

  // Optional: Log connection status for debugging
  useEffect(() => {
    setDebugMessages(prev => [...prev, `WebSocket Connected: ${isConnected}, Ready: ${isReady}`]);
  }, [isConnected, isReady]);

  // Helper functions to determine status icons
  const getCongestionStatusIcon = (val: number | undefined) => {
    if (val === undefined || val === null) return undefined; // Or a placeholder like HelpCircle
    if (val > 70) return TrendingDown; // High congestion is "bad"
    if (val > 40) return TrendingUp;   // Medium congestion is "warning" or "trending worse"
    return CheckCircle2; // Low congestion is "good"
  };

  const getSpeedStatusIcon = (val: number | undefined) => {
    if (val === undefined || val === null) return undefined;
    if (val < 20) return TrendingDown; // Low speed is "bad"
    if (val < 40) return TrendingUp;   // Medium speed is "warning"
    return CheckCircle2; // Good speed
  };

  const getIncidentStatusIcon = (val: number | undefined) => {
    if (val === undefined || val === null) return undefined;
    if (val > 0) return AlertTriangle; // Active incidents
    return ShieldCheck; // No incidents
  };
  // Total flow doesn't have a qualitative status here, so no specific icon based on value ranges.

  // Type guard for location object
  function isLatLng(obj: unknown): obj is { latitude: number; longitude: number } {
    return (
      typeof obj === 'object' &&
      obj !== null &&
      typeof (obj as { latitude?: unknown }).latitude === 'number' &&
      typeof (obj as { longitude?: unknown }).longitude === 'number'
    );
  }

  useEffect(() => {
    // Fetch congestion data from backend REST API
    const fetchKpisFromApi = async () => {
      const currentToken = await getToken(); // Get the latest token
      if (typeof currentToken !== 'string' || currentToken.length === 0) {
        // Only fetch if token is a non-empty string
        return;
      }
      try {
        const res = await fetch('/api/v1/analytics/nodes/congestion', {
          headers: {
            'Authorization': `Bearer ${currentToken}`,
          },
        });
        if (!res.ok) throw new Error(`Failed to fetch congestion data: ${res.status}`);
        const response = await res.json() as APIResponse<AllNodesCongestionResponse>;
        if (response.status === 'success' && response.data.nodes.length > 0) {
          const { nodes } = response.data;
          // Congestion Index
          const scores = nodes
            .map((n: BackendCongestionNodeData) => typeof n.congestion_score === 'number' ? n.congestion_score : null)
            .filter((v: number | null) => v !== null);
          if (scores.length > 0) {
            const avg = scores.reduce((a: number, b: number) => a + b, 0) / scores.length;
            setCongestionIndex(Number(avg.toFixed(1)));
          } else {
            setCongestionIndex(null);
          }
          // Average Speed
          const speeds = nodes
            .map((n: BackendCongestionNodeData) => typeof n.average_speed === 'number' ? n.average_speed : null)
            .filter((v: number | null) => v !== null);
          if (speeds.length > 0) {
            const avgSpeed = speeds.reduce((a: number, b: number) => a + b, 0) / speeds.length;
            setAverageSpeed(Number(avgSpeed.toFixed(1)));
          } else {
            setAverageSpeed(null);
          }
          // Total Flow (sum of vehicle_count)
          const vehicleCounts = nodes
            .map((n: BackendCongestionNodeData) => typeof n.vehicle_count === 'number' ? n.vehicle_count : null)
            .filter((v: number | null) => v !== null);
          if (vehicleCounts.length > 0) {
            const total = vehicleCounts.reduce((a: number, b: number) => a + b, 0);
            setTotalFlow(total);
          } else {
            setTotalFlow(null);
          }
          // Active Incidents: Not available in this endpoint, so keep using kpis or set to null
          setActiveIncidents(null); // Or use another endpoint if available
        } else {
          setCongestionIndex(null);
          setAverageSpeed(null);
          setTotalFlow(null);
          setActiveIncidents(null);
        }
      } catch {
        setCongestionIndex(null);
        setAverageSpeed(null);
        setTotalFlow(null);
        setActiveIncidents(null);
      }
    };

    fetchKpisFromApi(); // Call immediately
    const interval = setInterval(fetchKpisFromApi, 30000);
    return () => clearInterval(interval);
  }, []); // No dependency on 'token' anymore, as it's fetched inside

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
              value={`${congestionIndex !== null ? congestionIndex : '--'}%`}
              icon={Activity}
              statusIcon={getCongestionStatusIcon(typeof congestionIndex === 'number' ? congestionIndex : undefined)}
              change="N/A"
              changeText="Change data not available"
            />
            <StatCard
              title="Average Speed"
              value={`${averageSpeed !== null ? averageSpeed : '--'} km/h`}
              icon={Zap}
              statusIcon={getSpeedStatusIcon(typeof averageSpeed === 'number' ? averageSpeed : undefined)}
              change="N/A"
              changeText="Change data not available"
            />
            <StatCard
              title="Active Incidents"
              value={`${activeIncidents !== null ? activeIncidents : '--'}`}
              icon={AlertTriangle}
              statusIcon={getIncidentStatusIcon(typeof activeIncidents === 'number' ? activeIncidents : undefined)}
              change="N/A"
              changeText="Change data not available"
            />
            <StatCard
              title="Total Flow"
              value={`${totalFlow !== null ? totalFlow : '--'} vehicles/hr`}
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
              {sampleFeed ? (
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
              {/* Add more <div> blocks here for additional feeds if needed */}
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

          {/* WebSocket debug/messages (optional, using debugMessages now) */}
          <div className="matrix-card p-4">
            <h2 className="text-xl font-semibold mb-2 tracking-normal font-lcd matrix-glow text-lcd-text group-hover:text-lcd-bg">WEBSOCKET CONNECTION STATUS (DEBUG)</h2>
            <div className="max-h-60 overflow-y-auto text-sm font-lcd">
                {debugMessages.slice(-10).map((msg, index) => (
                    <p key={index} className="mb-1 break-all tracking-normal font-lcd matrix-glow text-lcd-text group-hover:text-lcd-bg">{msg}</p>
                ))}
                {debugMessages.length === 0 && <p className="text-lcd-text group-hover:text-lcd-bg tracking-normal font-lcd matrix-glow">MONITORING CONNECTION...</p>}
            </div>
          </div>
        </main>
      </div>
    </AuthGuard>
  );
};

export default DashboardPage;