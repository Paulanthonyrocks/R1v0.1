// frontend/app/dashboard/page.tsx
"use client";

import React, { useEffect, useState } from 'react';
import useSWR from 'swr';
import WebSocketClient from '@/lib/websocket'; // Adjust the import path if necessary
import AuthGuard from "@/components/auth/AuthGuard"; // Import AuthGuard
import { UserRole } from "@/lib/auth/roles"; // Import UserRole

const fetcher = (url: string) => fetch(url).then(res => res.json());

const MetricCard = ({ title, value, unit, color, children }: { title: string, value: React.ReactNode, unit?: string, color?: string, children?: React.ReactNode }) => (
  <div className={`bg-gray-800 p-4 rounded shadow text-center flex flex-col items-center justify-center min-w-[140px]`}>
    <div className="text-xs text-gray-400 uppercase mb-1">{title}</div>
    <div className={`text-2xl font-bold mb-1 ${color ? color : ''}`}>{value}{unit && <span className="text-base font-normal ml-1">{unit}</span>}</div>
    {children}
  </div>
);

const DashboardPage: React.FC = () => {
  // State to hold WebSocket messages (optional, for display/debugging)
  const [messages, setMessages] = useState<string[]>([]);

  // Fetch real-time analytics every 5 seconds
  const { data: metrics } = useSWR('/v1/analytics/realtime', fetcher, { refreshInterval: 5000 });

  useEffect(() => {
    // Instantiate the WebSocketClient
    // Provide the WebSocket URL. Adjust if your backend is on a different host/port/path.
    const ws = new WebSocketClient('ws://localhost:9002/ws'); 

    // Add event listeners
    const handleOpen = () => {
      console.log('WebSocket connection opened successfully in DashboardPage.');
      setMessages(prev => [...prev, 'WebSocket connection opened.']);
    };

    const handleMessage = (event: MessageEvent) => {
      console.log('WebSocket message received in DashboardPage:', event);
      // Assuming the event.data is a string or can be stringified
      try {
        // Attempt to parse JSON, but handle plain strings as well
        let messageData;
        try {
            messageData = JSON.parse(event.data as string);
        } catch {
            messageData = event.data; // Treat as plain string if JSON parsing fails
        }
        // Handle different message types (e.g., feed_metrics, kpi_update)
        setMessages(prev => [...prev, `Message: ${JSON.stringify(messageData, null, 2)}`]);
      } catch (error) {
        setMessages(prev => [...prev, `Raw Message: ${event.data}`]);
        console.error('Error processing WebSocket message:', error);
      }
    };

    const handleError = (event: Event) => {
      console.error('WebSocket error in DashboardPage:', event);
      // Attempt to get a more specific error message from the event
      const errorEvent = event as ErrorEvent;
      const errorMessage = errorEvent.message || (errorEvent.error ? String(errorEvent.error) : 'Unknown WebSocket error');
      setMessages(prev => [...prev, `WebSocket error: ${errorMessage}`]);
    };

    const handleClose = (event: CloseEvent) => {
      console.log('WebSocket connection closed in DashboardPage:', event?.code, event?.reason);
      setMessages(prev => [...prev, `WebSocket connection closed (Code: ${event.code}, Reason: ${event.reason}).`]);
    };

    // Add event listeners using addEventListener
    ws.addEventListener('open', handleOpen as EventListener);
    ws.addEventListener('message', handleMessage as EventListener);
    ws.addEventListener('error', handleError as EventListener);
    ws.addEventListener('close', handleClose as EventListener);

    // Clean up the WebSocket connection when the component unmounts
    return () => {
      console.log('Cleaning up WebSocket connection from DashboardPage.');
      // Remove event listeners to prevent memory leaks.
      ws.removeEventListener('open', handleOpen);
      ws.removeEventListener('message', handleMessage);
      ws.removeEventListener('error', handleError as EventListener); // Cast needed due to ErrorEvent vs generic Event
      ws.removeEventListener('close', handleClose);
    };
  }, []); // Empty dependency array ensures effect runs once on mount and cleans up on unmount

  // Helper to color metrics
  const getCongestionColor = (val: number) => val > 70 ? 'text-red-400' : val > 40 ? 'text-yellow-400' : 'text-green-400';
  const getSpeedColor = (val: number) => val < 20 ? 'text-red-400' : val < 40 ? 'text-yellow-400' : 'text-green-400';
  const getIncidentColor = (val: number) => val > 0 ? 'text-red-400' : 'text-green-400';

  return (
    <AuthGuard requiredRole={UserRole.VIEWER}> {/* Wrap content with AuthGuard and specify required role */}
      <div className="p-4 text-matrix">
        <h1 className="text-2xl font-bold mb-4 uppercase">Dashboard</h1>

        {/* Real-time Analytics Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <MetricCard title="Congestion Index" value={metrics?.congestion_index ?? '--'} unit="%" color={metrics ? getCongestionColor(metrics.congestion_index) : ''} />
          <MetricCard title="Average Speed" value={metrics?.average_speed_kmh ?? '--'} unit="km/h" color={metrics ? getSpeedColor(metrics.average_speed_kmh) : ''} />
          <MetricCard title="Active Incidents" value={metrics?.active_incidents_count ?? '--'} color={metrics ? getIncidentColor(metrics.active_incidents_count) : ''} />
          <MetricCard title="Feeds" value={metrics?.feed_statuses ? metrics.feed_statuses.running : '--'} unit="/ running">
            <div className="text-xs text-gray-400 mt-1">
              {metrics?.feed_statuses && (
                <>
                  <span className="mr-2">Stopped: <span className="text-yellow-400">{metrics.feed_statuses.stopped}</span></span>
                  <span>Error: <span className="text-red-400">{metrics.feed_statuses.error}</span></span>
                </>
              )}
            </div>
          </MetricCard>
        </div>

        {/* Placeholder for video stream */}
        <div className="mb-4 bg-gray-800 p-4 rounded">
          <h2 className="text-xl font-semibold mb-2">Video Feed (Sample)</h2>
          <div className="w-full h-96 bg-gray-700 flex items-center justify-center text-gray-400 rounded">
            <video
              src="/api/v1/sample-video"
              controls
              autoPlay
              className="w-full h-full object-cover rounded"
              style={{ background: '#000' }}
            />
          </div>
        </div>

        {/* WebSocket debug/messages */}
        <div className="bg-gray-800 p-4 rounded">
          <h2 className="text-xl font-semibold mb-2">Real-time Data (WebSocket Debug)</h2>
          <div className="max-h-60 overflow-y-auto text-sm">
              {messages.map((msg, index) => (
                  <p key={index} className="mb-1 break-all">{msg}</p>
              ))}
              {messages.length === 0 && <p className="text-gray-400">Waiting for messages...</p>}
          </div>
        </div>

         {/* Add controls for start/stop feed later */}

      </div>
    </AuthGuard>
  );
};

export default DashboardPage;