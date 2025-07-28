'use client';

import React, { useEffect } from 'react';
import AuthGuard from '@/components/auth/AuthGuard';
import { UserRole } from '@/lib/auth/roles';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import useAuth from '@/lib/hook/useAuth'; // Import useAuth
import SurveillanceFeed from '@/components/dashboard/SurveillanceFeed';
import { FeedStatusData } from '@/lib/types';
import LoadingMessage from '@/components/ui/LoadingMessage';
import { Signal, Clock, BatteryFull } from 'lucide-react';

const SurveillancePage = () => {
  const { token } = useAuth();
  const { feeds, isConnected, isReady, startWebSocket } = useRealtimeUpdates(token);

  useEffect(() => {
    // Start WebSocket connection on component mount
    if (!isConnected) { // Optionally check if already connected if hook supports it
        console.log("SurveillancePage: Attempting to start WebSocket connection.");
        startWebSocket();
    }
    // No explicit cleanup needed here as the hook manages its own lifecycle
  }, [startWebSocket, isConnected]);

  const renderContent = () => {
    if (!isReady || !isConnected) {
      return <LoadingMessage text="Connecting to surveillance system..." />;
    }

    if (feeds.length === 0) {
      return (
        <div className="flex justify-center items-center h-64">
          <p className="text-lcd-text text-lg tracking-normal">No surveillance feeds available at the moment.</p>
        </div>
      );
    }

    return (
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
        {feeds.map((feed: FeedStatusData) => (
          <SurveillanceFeed
            key={feed.id}
            feed={feed}
          />
        ))}
      </div>
    );
  };

  return (
    <AuthGuard requiredRole={UserRole.AGENCY}>
      <div className="bg-lcd-bg text-lcd-text font-lcd flex flex-col min-h-screen w-full">
        {/* Status Bar */}
        <header className="flex items-center justify-between px-4 py-1 border-b-2 border-lcd-text">
          <div className="flex items-center space-x-2">
            <Signal size={20} />
            <span className="font-lcd matrix-glow">SURVEILLANCE</span>
          </div>
          <div className="flex items-center space-x-2">
            <Clock size={20} />
            <span className="font-lcd matrix-glow">{new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
            <BatteryFull size={20} />
          </div>
        </header>
        <main className="flex-1 p-4">
          <h1 className="text-2xl font-bold mb-6 uppercase text-lcd-text tracking-normal">SURVEILLANCE FEEDS</h1>
          {renderContent()}
        </main>
      </div>
    </AuthGuard>
  );
};

export default SurveillancePage;
