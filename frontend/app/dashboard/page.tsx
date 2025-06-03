// frontend/app/dashboard/page.tsx
"use client";

// frontend/app/dashboard/page.tsx
"use client";

import React, { useEffect, useState } from 'react';
// import useSWR from 'swr'; // Removed useSWR
// Removed WebSocketClient import, will be handled by the hook
import AuthGuard from "@/components/auth/AuthGuard"; // Import AuthGuard
import { UserRole } from "@/lib/auth/roles"; // Import UserRole
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates'; // Import the hook
import AnomalyItem from '@/components/dashboard/AnomalyItem'; // Import AnomalyItem

// const fetcher = (url: string) => fetch(url).then(res => res.json()); // Removed fetcher, no longer needed by SWR

const MetricCard = ({ title, value, unit, color, children }: { title: string, value: React.ReactNode, unit?: string, color?: string, children?: React.ReactNode }) => (
  <div className={`bg-gray-800 p-4 rounded shadow text-center flex flex-col items-center justify-center min-w-[140px]`}>
    <div className="text-xs text-gray-400 uppercase mb-1">{title}</div>
    <div className={`text-2xl font-bold mb-1 ${color ? color : ''}`}>{value}{unit && <span className="text-base font-normal ml-1">{unit}</span>}</div>
    {children}
  </div>
);

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

  // Helper to color metrics (can be kept or adapted)
  const getCongestionColor = (val: number) => val > 70 ? 'text-red-400' : val > 40 ? 'text-yellow-400' : 'text-green-400';
  const getSpeedColor = (val: number) => val < 20 ? 'text-red-400' : val < 40 ? 'text-yellow-400' : 'text-green-400';
  const getIncidentColor = (val: number) => val > 0 ? 'text-red-400' : 'text-green-400';

  return (
    <AuthGuard requiredRole={UserRole.PLANNER}> {/* Wrap content with AuthGuard and specify required role */}
      <div className="p-4 text-matrix">
        <h1 className="text-2xl font-bold mb-4 uppercase">Dashboard</h1>

        {/* Navigation Links */}
        <nav className="mb-6 flex gap-4">
          <a href="/preferences" className="px-3 py-1 bg-blue-600 text-white rounded hover:bg-blue-700 transition">Preferences</a>
          <a href="/history" className="px-3 py-1 bg-blue-600 text-white rounded hover:bg-blue-700 transition">Route History</a>
          <a href="/impacts" className="px-3 py-1 bg-blue-600 text-white rounded hover:bg-blue-700 transition">Weather & Events</a>
        </nav>

        {/* Real-time Analytics Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <MetricCard
            title="Congestion Index"
            value={kpis?.congestion_index ?? '--'}
            unit="%"
            color={kpis?.congestion_index ? getCongestionColor(kpis.congestion_index) : 'text-gray-400'}
          />
          <MetricCard
            title="Average Speed"
            value={kpis?.average_speed_kmh ?? '--'}
            unit="km/h"
            color={kpis?.average_speed_kmh ? getSpeedColor(kpis.average_speed_kmh) : 'text-gray-400'}
          />
          <MetricCard
            title="Active Incidents"
            value={kpis?.active_incidents_count ?? '--'}
            color={kpis?.active_incidents_count ? getIncidentColor(kpis.active_incidents_count) : 'text-gray-400'}
          />
          <MetricCard
            title="Total Flow"
            value={kpis?.total_flow ?? '--'}
            unit="vehicles/hr"
            color={kpis?.total_flow ? undefined : 'text-gray-400'} // No specific color logic for flow, default if no value
          />
        </div>

        {/* Placeholder for video stream */}
        <div className="mb-4 bg-gray-800 p-4 rounded">
          <h2 className="text-xl font-semibold mb-2">Video Feed (Sample)</h2>
          <div className="w-full h-96 bg-gray-700 flex items-center justify-center text-gray-400 rounded">
            <video
              src="/api/v1/sample-video"
              controls
              autoPlay
              className="w-full h-full object-cover rounded bg-black"
            />
          </div>
        </div>

        {/* Live Alerts Section */}
        <div className="mb-6 bg-gray-800 p-4 rounded">
          <h2 className="text-xl font-semibold mb-3">Live Alerts</h2>
          {!isReady && <p className="text-gray-400">Connecting to live alerts...</p>}
          {isReady && alerts.length === 0 && <p className="text-gray-400">No new alerts.</p>}
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
        <div className="bg-gray-800 p-4 rounded">
          <h2 className="text-xl font-semibold mb-2">WebSocket Connection Status (Debug)</h2>
          <div className="max-h-60 overflow-y-auto text-sm">
              {debugMessages.slice(-10).map((msg, index) => (
                  <p key={index} className="mb-1 break-all">{msg}</p>
              ))}
              {debugMessages.length === 0 && <p className="text-gray-400">Monitoring connection...</p>}
          </div>
        </div>

      </div>
    </AuthGuard>
  );
};

export default DashboardPage;