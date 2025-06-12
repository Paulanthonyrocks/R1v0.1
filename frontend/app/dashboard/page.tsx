// frontend/app/dashboard/page.tsx
"use client";

// frontend/app/dashboard/page.tsx
"use client";

import React, { useEffect, useState } from 'react';
import AuthGuard from "@/components/auth/AuthGuard"; // Import AuthGuard
import { UserRole } from "@/lib/auth/roles"; // Import UserRole
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates'; // Import the hook
import AnomalyItem from '@/components/dashboard/AnomalyItem'; // Import AnomalyItem
import StatCard from '@/components/dashboard/StatCard'; // Import StatCard
import {
  Activity, Zap, AlertTriangle, Users, TrendingDown, TrendingUp, CheckCircle2, ShieldCheck
} from 'lucide-react'; // Import Lucide icons & new status icons

const DashboardPage: React.FC = () => {
  // State to hold WebSocket messages (optional, for display/debugging) - can be removed or adapted
  const [debugMessages, setDebugMessages] = useState<string[]>([]);

  // Use the realtime updates hook - This is now the primary source for KPIs
  const { kpis, alerts, isConnected, isReady, startWebSocket } = useRealtimeUpdates('ws://localhost:9002/ws');
  // const { data: metrics } = useSWR('/v1/analytics/realtime', fetcher, { refreshInterval: 5000 }); // Removed SWR

  useEffect(() => {
    // Start WebSocket connection on component mount
    console.log("DashboardPage: Attempting to start WebSocket connection.");
    startWebSocket();
    // No explicit cleanup needed here as the hook manages its own lifecycle
  }, [startWebSocket]); // Dependency array includes startWebSocket as it's a stable function reference from the hook

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

  return (
    <AuthGuard requiredRole={UserRole.PLANNER}> {/* Wrap content with AuthGuard and specify required role */}
      <div className="p-4 text-matrix">
        <h1 className="text-2xl font-bold mb-4 uppercase tracking-normal">Dashboard</h1> {/* Added tracking-normal */}

        {/* Navigation Links */}
        <nav className="mb-6 flex gap-4">
          <a href="/preferences" className="px-3 py-1 bg-primary text-primary-foreground hover:bg-primary/90 rounded transition">Preferences</a>
          <a href="/history" className="px-3 py-1 bg-primary text-primary-foreground hover:bg-primary/90 rounded transition">Route History</a>
          <a href="/impacts" className="px-3 py-1 bg-primary text-primary-foreground hover:bg-primary/90 rounded transition">Weather & Events</a>
        </nav>

        {/* Real-time Analytics Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <StatCard
            title="Congestion Index"
            value={`${kpis?.congestion_index ?? '--'}%`}
            icon={Activity}
            statusIcon={getCongestionStatusIcon(kpis?.congestion_index)}
            change="N/A"
            changeText="Change data not available"
          />
          <StatCard
            title="Average Speed"
            value={`${kpis?.average_speed_kmh ?? '--'} km/h`}
            icon={Zap}
            statusIcon={getSpeedStatusIcon(kpis?.average_speed_kmh)}
            change="N/A"
            changeText="Change data not available"
          />
          <StatCard
            title="Active Incidents"
            value={`${kpis?.active_incidents_count ?? '--'}`}
            icon={AlertTriangle} // Main icon for the card category
            statusIcon={getIncidentStatusIcon(kpis?.active_incidents_count)} // Status icon next to value
            change="N/A"
            changeText="Change data not available"
          />
          <StatCard
            title="Total Flow"
            value={`${kpis?.total_flow ?? '--'} vehicles/hr`}
            icon={Users}
            // No specific statusIcon for total flow based on current logic
            change="N/A"
            changeText="Change data not available"
          />
        </div>

        {/* Placeholder for video stream */}
        <div className="mb-4 bg-card p-4 rounded border border-primary pixel-drop-shadow"> {/* Added border and shadow */}
          <h2 className="text-xl font-semibold mb-2 tracking-normal">Video Feed (Sample)</h2> {/* Added tracking-normal */}
          <div className="w-full h-96 bg-background flex items-center justify-center text-muted-foreground rounded">
            <video
              src="/api/v1/sample-video"
              controls
              autoPlay
              className="w-full h-full object-cover rounded bg-black"
            />
          </div>
        </div>

        {/* Live Alerts Section */}
        <div className="mb-6 bg-card p-4 rounded border border-primary pixel-drop-shadow"> {/* Added border and shadow */}
          <h2 className="text-xl font-semibold mb-3 tracking-normal">Live Alerts</h2> {/* Added tracking-normal */}
          {!isReady && <p className="text-muted-foreground tracking-normal">Connecting to live alerts...</p>} {/* Added tracking-normal */}
          {isReady && alerts.length === 0 && <p className="text-muted-foreground tracking-normal">No new alerts.</p>} {/* Added tracking-normal */}
          {isReady && alerts.length > 0 && (
            <div className="space-y-3 max-h-96 overflow-y-auto">
              {alerts.slice(-10).reverse().map((alert) => (
                <AnomalyItem
                  key={alert.id || new Date(alert.timestamp).toISOString()} // Assuming alert has an id, fallback to timestamp
                  timestamp={new Date(alert.timestamp).toLocaleString()}
                  severity={alert.severity || 'info'} // Assuming severity exists
                  message={alert.message}
                  location={alert.details?.location ? `Lat: ${alert.details.location.latitude}, Lon: ${alert.details.location.longitude}` : 'N/A'}
                  details={alert.details ? JSON.stringify(alert.details, null, 2) : undefined}
                />
              ))}
            </div>
          )}
        </div>

        {/* WebSocket debug/messages (optional, using debugMessages now) */}
        <div className="bg-card p-4 rounded border border-primary pixel-drop-shadow"> {/* Added border and shadow */}
          <h2 className="text-xl font-semibold mb-2 tracking-normal">WebSocket Connection Status (Debug)</h2> {/* Added tracking-normal */}
          <div className="max-h-60 overflow-y-auto text-sm text-muted-foreground">
              {debugMessages.slice(-10).map((msg, index) => (
                  <p key={index} className="mb-1 break-all tracking-normal">{msg}</p> /* Added tracking-normal */
              ))}
              {debugMessages.length === 0 && <p className="text-muted-foreground tracking-normal">Monitoring connection...</p>} {/* Added tracking-normal */}
          </div>
        </div>

      </div>
    </AuthGuard>
  );
};

export default DashboardPage;