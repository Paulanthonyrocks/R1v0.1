'use client';

import React, { useEffect } from 'react'; // Removed useState, MatrixCard, useSWR
import AuthGuard from '@/components/auth/AuthGuard';
import { UserRole } from '@/lib/auth/roles';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates'; // Import the hook
import SurveillanceFeed from '@/components/dashboard/SurveillanceFeed'; // Import SurveillanceFeed component
import { FeedStatusData } from '@/lib/types'; // To type the feed items

const LoadingMessage = ({ text }: { text: string }) => (
  <div className="flex justify-center items-center h-64">
    <p className="text-matrix text-xl animate-pulse">{text}</p>
  </div>
);

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
                <h1 className="text-2xl font-bold mb-4 uppercase text-matrix">SURVEILLANCE</h1>
                <LoadingMessage text="Connecting to surveillance system..." />
            </div>
        </AuthGuard>
    );
  }

  return (
    <AuthGuard requiredRole={UserRole.AGENCY}>
      <div className="p-4 w-full relative">
        <h1 className="text-2xl font-bold mb-6 uppercase text-matrix">SURVEILLANCE FEEDS</h1>

        {feeds.length === 0 ? (
          <div className="flex justify-center items-center h-64">
            <p className="text-matrix-muted text-lg">No surveillance feeds available at the moment.</p>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
            {feeds.map((feed: FeedStatusData) => (
              <SurveillanceFeed
                key={feed.id}
                id={feed.id}
                name={feed.name ?? `Feed ${feed.id}`} // Use name or fallback to ID-based name
                // The `node` prop for SurveillanceFeed was for a display string like "#TC-142".
                // `FeedStatusData` doesn't have a direct `node` field.
                // Using `feed.source` or a constructed string as a placeholder.
                node={`Source: ${feed.source}`}
                // The SurveillanceFeed component itself handles the video source via `feed.source`
                // and also uses the `status` from `FeedStatusData` internally if designed so.
                // For this refactor, we ensure `SurveillanceFeed` gets the full `feed` object
                // or individual props as needed. `SurveillanceFeedProps` expects `id`, `name`, `node`.
                // The actual video URL is derived from `feed.source` within `SurveillanceFeed` component.
                // We pass `feed.status` and `feed.fps` if `SurveillanceFeed` is updated to use them.
                // For now, sticking to what `SurveillanceFeedProps` strictly defines, plus `feed` itself.
                status={feed.status} // Pass status if SurveillanceFeed can use it
                fps={feed.fps}       // Pass FPS if SurveillanceFeed can use it
                source={feed.source} // Pass source for video URL construction within SurveillanceFeed
              />
            ))}
          </div>
        )}
      </div>
    </AuthGuard>
  );
};

export default SurveillancePage;