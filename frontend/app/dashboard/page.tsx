// frontend/app/dashboard/page.tsx
"use client";

import React, { useEffect, useState } from 'react';
import WebSocketClient from '@/lib/websocket'; // Adjust the import path if necessary
import AuthGuard from "@/components/auth/AuthGuard"; // Import AuthGuard
import { UserRole } from "@/lib/auth/roles"; // Import UserRole

const DashboardPage: React.FC = () => {
  // State to hold WebSocket messages (optional, for display/debugging)
  const [messages, setMessages] = useState<string[]>([]);
  const [isConnected, setIsConnected] = useState(false);

  useEffect(() => {
    // Instantiate the WebSocketClient
    // Provide the WebSocket URL. Adjust if your backend is on a different host/port/path.
    const ws = new WebSocketClient('ws://localhost:9002/ws'); 

    // Add event listeners
    const handleOpen = () => {
      console.log('WebSocket connection opened successfully in DashboardPage.');
      setIsConnected(true);
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
      setIsConnected(false);
      // Attempt to get a more specific error message from the event
      const errorEvent = event as ErrorEvent;
      const errorMessage = errorEvent.message || (errorEvent.error ? String(errorEvent.error) : 'Unknown WebSocket error');
      setMessages(prev => [...prev, `WebSocket error: ${errorMessage}`]);
    };

    const handleClose = (event: CloseEvent) => {
      console.log('WebSocket connection closed in DashboardPage:', event?.code, event?.reason);
      setIsConnected(false);
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

  return (
    <AuthGuard requiredRole={UserRole.VIEWER}> {/* Wrap content with AuthGuard and specify required role */}
      <div className="p-4 text-matrix">
        <h1 className="text-2xl font-bold mb-4 uppercase">Dashboard</h1>
        <p>WebSocket Status: {isConnected ? 'Connected' : 'Disconnected'}</p>

        {/* Placeholder for video stream */}
        <div className="mb-4 bg-gray-800 p-4 rounded">
          <h2 className="text-xl font-semibold mb-2">Video Feed (Sample)</h2>
          {/* Video element would go here */}
          <div className="w-full h-96 bg-gray-700 flex items-center justify-center text-gray-400 rounded">
            Placeholder for video stream
          </div>
        </div>

        {/* Placeholder for metrics/anomalies */}
        <div className="bg-gray-800 p-4 rounded">
          <h2 className="text-xl font-semibold mb-2">Real-time Data</h2>
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