'use client';

import React, { useEffect } from 'react'; // Removed useState, MatrixCard, useSWR
import AuthGuard from '@/components/auth/AuthGuard';
import { UserRole } from '@/lib/auth/roles';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates'; // Import the hook
import SurveillanceFeed from '@/components/dashboard/SurveillanceFeed'; // Import SurveillanceFeed component
import { FeedStatusData } from '@/lib/types'; // To type the feed items
import LoadingMessage from '@/components/ui/LoadingMessage'; // Import the new LoadingMessage component

const SurveillancePage = () => {
  const { feeds, isConnected, isReady, startWebSocket } = useRealtimeUpdates('ws://localhost:9002/ws');

  useEffect(() => {
    // Start WebSocket connection on component mount
    if (!isConnected) { // Optionally check if already connected if hook supports it
        console.log("SurveillancePage: Attempting to start WebSocket connection.");
        startWebSocket();
    }
    // No explicit cleanup needed here as the hook manages its own lifecycle
  }, [startWebSocket, isConnected]);

  if (!isReady || !isConnected) {
    return (
        <AuthGuard requiredRole={UserRole.AGENCY}>
            <div className="p-4 w-full relative">
                <h1 className="text-2xl font-bold mb-4 uppercase text-matrix tracking-normal">SURVEILLANCE</h1> {/* Added tracking-normal */}
                <LoadingMessage text="Connecting to surveillance system..." />
            </div>
        </AuthGuard>
    );
  }

  return (
    <AuthGuard requiredRole={UserRole.AGENCY}>
      <div className="p-4 w-full relative">
        <h1 className="text-2xl font-bold mb-6 uppercase text-matrix tracking-normal">SURVEILLANCE FEEDS</h1> {/* Added tracking-normal */}

        {feeds.length === 0 ? (
          <div className="flex justify-center items-center h-64">
            <p className="text-matrix-muted text-lg tracking-normal">No surveillance feeds available at the moment.</p> {/* Added tracking-normal */}
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {feeds.map((feed: FeedStatusData) => (
              <SurveillanceFeed
                key={feed.id}
                feed={feed} // Prop was already updated in previous step
              />
            ))}
          </div>
        )}
      </div>
    </AuthGuard>
  );
};

export default SurveillancePage;