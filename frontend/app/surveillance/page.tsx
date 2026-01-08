'use client';

import React, { useState, useMemo } from 'react';
import AuthGuard from '@/components/auth/AuthGuard';
import { UserRole } from '@/lib/auth/roles';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';

import SurveillanceMatrix from '@/components/dashboard/SurveillanceMatrix';
import { FeedStatusData } from '@/lib/types';
import { Search, Play, Square, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import DashboardShell from '@/components/dashboard/DashboardShell';
import AddFeedDialog from '@/components/dashboard/AddFeedDialog';

const SurveillancePage = () => {
  const { feeds, isConnected, isReady, startFeed, stopFeed } = useRealtimeUpdates();
  const [searchQuery, setSearchQuery] = useState('');

  const filteredFeeds = useMemo(() => {
    return feeds.filter(feed => 
      (feed.name || feed.feed_id).toLowerCase().includes(searchQuery.toLowerCase()) ||
      feed.source.toLowerCase().includes(searchQuery.toLowerCase())
    );
  }, [feeds, searchQuery]);

  const handleStartAll = () => {
    feeds.forEach(f => {
      if (f.status === 'stopped' || f.status === 'error') startFeed(f.feed_id);
    });
  };

  const handleStopAll = () => {
    feeds.forEach(f => {
      if (f.status === 'running' || f.status === 'starting') stopFeed(f.feed_id);
    });
  };

  const renderContent = () => {
    if (!isReady || !isConnected) {
      return (
        <div className="flex flex-col items-center justify-center h-96 opacity-50 text-lcd-text">
            <RefreshCw className="animate-spin mb-4" size={48} />
            <p className="tracking-[0.2em] font-bold uppercase">Negotiating Handshake...</p>
        </div>
      );
    }

    if (feeds.length === 0) {
      return (
        <div className="flex flex-col justify-center items-center h-96 border-2 border-dashed border-lcd-text/20 rounded">
          <p className="text-lcd-text text-lg tracking-widest uppercase font-bold mb-2">No Uplinks Found</p>
          <p className="text-xs opacity-60 uppercase text-lcd-text">Add feeds via configuration to begin monitoring</p>
        </div>
      );
    }

    if (filteredFeeds.length === 0) {
        return (
            <div className="flex justify-center items-center h-64 opacity-50 text-lcd-text">
                <p className="text-lg tracking-widest uppercase font-bold">No Match for &apos;{searchQuery}&apos;</p>
            </div>
        );
    }

    return (
      <div className="h-[calc(100vh-350px)] min-h-[500px]">
        <SurveillanceMatrix feeds={filteredFeeds} />
      </div>
    );
  };

  return (
    <AuthGuard requiredRole={UserRole.AGENCY}>
      <DashboardShell>
          <div className="flex flex-col lg:flex-row justify-between items-end mb-8 gap-6">
              <div>
                  <h1 className="text-4xl font-bold uppercase tracking-tighter mb-2 text-lcd-text matrix-glow">Omni-Channel Monitoring</h1>
                  <p className="text-lcd-text/60 max-w-2xl">
                      Live video processing via YOLOv8. Manage individual node states or broadcast global commands 
                      to the processing cluster.
                  </p>
              </div>
              
              <div className="flex flex-col sm:flex-row gap-4 w-full lg:w-auto">
                  <div className="relative flex-1 sm:w-80">
                      <Search className="absolute left-3 top-1/2 -translate-y-1/2 opacity-40 text-lcd-text" size={18} />
                      <input
                        type="text"
                        placeholder="FILTER FEEDS..."
                        className="bg-lcd-text/10 border-2 border-lcd-text/20 text-lcd-text rounded-none p-3 pl-10 w-full focus:outline-none focus:border-lcd-text tracking-[0.1em] placeholder:text-lcd-text/30 font-bold"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                      />
                  </div>
                  <div className="flex gap-2">
                      <AddFeedDialog />
                      <Button 
                        onClick={handleStartAll}
                        variant="outline" 
                        className="flex-1 sm:flex-none bg-green-900/20 border-green-500/50 text-green-900 hover:bg-green-500 hover:text-white rounded-none uppercase font-bold"
                      >
                          <Play size={16} className="mr-2" /> Start All
                      </Button>
                      <Button 
                        onClick={handleStopAll}
                        variant="outline" 
                        className="flex-1 sm:flex-none bg-red-900/20 border-red-500/50 text-red-900 hover:bg-red-500 hover:text-white rounded-none uppercase font-bold"
                      >
                          <Square size={16} className="mr-2" /> Stop All
                      </Button>
                  </div>
              </div>
          </div>

          <div className="mb-6 flex items-center justify-between border-b border-lcd-text/10 pb-4 text-lcd-text">
              <div className="flex items-center gap-6 text-[10px] uppercase font-bold opacity-70">
                  <div className="flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full bg-green-500"></span>
                      Running: {feeds.filter(f => f.status === 'running').length}
                  </div>
                  <div className="flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full bg-yellow-500"></span>
                      Starting: {feeds.filter(f => f.status === 'starting').length}
                  </div>
                  <div className="flex items-center gap-2">
                      <span className="w-2 h-2 rounded-full bg-red-500"></span>
                      Stopped/Error: {feeds.filter(f => f.status === 'stopped' || f.status === 'error').length}
                  </div>
              </div>
              <div className="text-[10px] opacity-40 uppercase">
                  Showing {filteredFeeds.length} of {feeds.length} clusters
              </div>
          </div>

          {renderContent()}
      </DashboardShell>
    </AuthGuard>
  );
};

export default SurveillancePage;