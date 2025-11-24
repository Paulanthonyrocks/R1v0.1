'use client';

import React, { useEffect } from 'react';
import Link from 'next/link'; // Import Link
import AuthGuard from '@/components/auth/AuthGuard';
import { UserRole } from '@/lib/auth/roles';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';

import SurveillanceFeed from '@/components/dashboard/SurveillanceFeed';
import { FeedStatusData } from '@/lib/types';
import LoadingMessage from '@/components/ui/LoadingMessage';
import { Signal, Clock, BatteryFull } from 'lucide-react';
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from "@/components/ui/dropdown-menu"; // Import DropdownMenu components

const SurveillancePage = () => {
  
  const { feeds, isConnected, isReady } = useRealtimeUpdates();

  useEffect(() => {
    // Connection is handled by WebSocketProvider.
    // This effect can be used to react to connection status changes.
    console.log(`SurveillancePage: WebSocket connection status: ${isConnected ? 'Connected' : 'Disconnected'}`);
  }, [isConnected]);

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
            key={feed.feed_id}
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
        <header className="bg-lcd-bg text-lcd-text font-lcd flex items-center justify-between px-4 py-1 border-b-2 border-lcd-text">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <div className="flex items-center space-x-2 cursor-pointer">
                <Signal size={20} />
                <span className="font-lcd matrix-glow">SURVEILLANCE</span>
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
                <Link href="/dashboard" className="w-full tracking-normal font-lcd matrix-glow">DASHBOARD</Link>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
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
