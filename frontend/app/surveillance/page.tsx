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
          <div className="retro-title-container">
              <div className="flex flex-col lg:flex-row justify-between items-end gap-6">
                  <div>
                      <h1 className="text-5xl font-black uppercase tracking-tighter font-lcd matrix-glow text-lcd-text mb-1">Surveillance Matrix</h1>
                      <div className="flex items-center gap-2">
                          <span className="terminal-text text-[10px]">UPLINK.SECURE // LIVE_VIDEO_PROCESSOR_v8</span>
                      </div>
                  </div>
                  
                  <div className="flex flex-col sm:flex-row gap-4 w-full lg:w-auto items-center">
                      <div className="relative flex-1 sm:w-80 font-bold group">
                          <Search className="absolute left-3 top-1/2 -translate-y-1/2 opacity-40 group-focus-within:opacity-100 transition-opacity" size={18} />
                          <input
                            type="text"
                            placeholder="SEARCH NODE REGISTRY..."
                            className="bg-lcd-text/5 border-2 border-lcd-text/30 text-lcd-text rounded-none p-4 pl-12 w-full focus:outline-none focus:border-lcd-text focus:bg-lcd-text/10 tracking-[0.2em] placeholder:text-lcd-text/20 font-black uppercase transition-all"
                            value={searchQuery}
                            onChange={(e) => setSearchQuery(e.target.value)}
                          />
                      </div>
                      <div className="flex gap-3">
                          <AddFeedDialog />
                          <Button 
                            onClick={handleStartAll}
                            className="matrix-btn-sleek h-12 px-6 bg-green-500/10 text-green-700 border-green-600 hover:bg-green-500 hover:text-white"
                          >
                              <Play size={16} className="mr-2" /> Global Start
                          </Button>
                          <Button 
                            onClick={handleStopAll}
                            className="matrix-btn-sleek h-12 px-6 bg-red-500/10 text-red-700 border-red-600 hover:bg-red-500 hover:text-white"
                          >
                              <Square size={16} className="mr-2" /> Global Stop
                          </Button>
                      </div>
                  </div>
              </div>
          </div>

          <div className="mb-8 flex items-center justify-between border-b-2 border-lcd-text/20 pb-4">
              <div className="flex items-center gap-8 text-[10px] uppercase font-black tracking-widest">
                  <div className="flex items-center gap-3 bg-green-500/10 px-3 py-1 border border-green-500/30">
                      <div className="w-2 h-2 rounded-full bg-green-600 animate-pulse"></div>
                      <span>ONLINE: {feeds.filter(f => f.status === 'running').length}</span>
                  </div>
                  <div className="flex items-center gap-3 bg-yellow-500/10 px-3 py-1 border border-yellow-500/30">
                      <div className="w-2 h-2 rounded-full bg-yellow-600"></div>
                      <span>INITIALIZING: {feeds.filter(f => f.status === 'starting').length}</span>
                  </div>
                  <div className="flex items-center gap-3 bg-red-500/10 px-3 py-1 border border-red-500/30 text-red-700">
                      <div className="w-2 h-2 rounded-full bg-red-600"></div>
                      <span>OFFLINE: {feeds.filter(f => f.status === 'stopped' || f.status === 'error').length}</span>
                  </div>
              </div>
              <div className="text-[10px] font-bold opacity-30 uppercase tracking-[0.2em]">
                  Registry Index: {filteredFeeds.length} / {feeds.length} Active
              </div>
          </div>

          <div className="min-h-[600px] flex flex-col">
              {renderContent()}
          </div>
      </DashboardShell>
    </AuthGuard>
  );
};


export default SurveillancePage;