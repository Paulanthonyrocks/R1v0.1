"use client";

import React, { useState, useEffect } from 'react';
import { FeedStatusData } from '@/lib/types';
import SurveillanceFeed from './SurveillanceFeed';
import { Button } from '@/components/ui/button';
import { Grid, LayoutTemplate, Maximize2, Minimize2, VideoOff } from 'lucide-react';
import { cn } from '@/lib/utils';

interface SurveillanceMatrixProps {
  feeds: FeedStatusData[];
}

const SurveillanceMatrix: React.FC<SurveillanceMatrixProps> = ({ feeds }) => {
  const [focusedFeedId, setFocusedFeedId] = useState<string | null>(null);
  const [layoutMode, setLayoutMode] = useState<'grid' | 'focus'>('grid');
  
  // Filter active feeds or all feeds depending on preference.
  // Currently showing all feeds including stopped ones (so we can start them)
  const activeFeeds = feeds;

  const handleFeedClick = (feedId: string) => {
    if (layoutMode === 'grid') {
      setFocusedFeedId(feedId);
      setLayoutMode('focus');
    } else {
        // In focus mode, clicking might not do anything unless it's a thumbnail
        setFocusedFeedId(feedId);
    }
  };

  const toggleLayout = () => {
      if (layoutMode === 'focus') {
          setLayoutMode('grid');
          setFocusedFeedId(null);
      } else {
          if (activeFeeds.length > 0) {
              setFocusedFeedId(activeFeeds[0].feed_id);
              setLayoutMode('focus');
          }
      }
  };

  return (
    <div className="w-full space-y-4">
        {/* Matrix Toolbar */}
        <div className="matrix-card p-0 overflow-hidden">
            <div className="matrix-card-header h-12">
                <div className="flex items-center gap-4">
                    <h3 className="text-xs font-bold uppercase tracking-widest font-lcd">
                        NODE_MATRIX_RENDER: <span className="text-primary">{layoutMode.toUpperCase()}</span>
                    </h3>
                    <div className="h-4 w-px bg-lcd-text/20"></div>
                    <div className="flex gap-1">
                        <Button 
                            variant="ghost" 
                            size="sm" 
                            onClick={() => { setLayoutMode('grid'); setFocusedFeedId(null); }}
                            className={cn("h-8 px-3 rounded-none font-bold uppercase text-[10px]", layoutMode === 'grid' ? 'bg-lcd-text text-lcd-bg' : 'hover:bg-lcd-text/10')}
                        >
                            <Grid size={14} className="mr-2" /> Grid
                        </Button>
                        <Button 
                            variant="ghost" 
                            size="sm" 
                            onClick={() => { if (feeds.length > 0) { setLayoutMode('focus'); setFocusedFeedId(feeds[0].feed_id); }}}
                            className={cn("h-8 px-3 rounded-none font-bold uppercase text-[10px]", layoutMode === 'focus' ? 'bg-lcd-text text-lcd-bg' : 'hover:bg-lcd-text/10')}
                            disabled={feeds.length === 0}
                        >
                            <Maximize2 size={14} className="mr-2" /> Focus
                        </Button>
                    </div>
                </div>
                
                <div className="text-[10px] font-bold opacity-60 uppercase tracking-widest">
                    Telemetry Uplinks: {feeds.filter(f => f.status === 'running').length} / {feeds.length}
                </div>
            </div>
        </div>

        {/* Matrix Content */}
        {feeds.length === 0 ? (
            <div className="flex-1 flex flex-col items-center justify-center border-4 border-dashed border-lcd-text/10 bg-lcd-text/5 p-12 opacity-30">
                <VideoOff size={64} className="mb-6 opacity-20" />
                <p className="tracking-widest font-bold text-2xl uppercase">Sensor Array Offline</p>
                <p className="text-xs mt-4 font-bold uppercase tracking-widest">Awaiting local stream initialization</p>
            </div>
        ) : (
            <div className="relative">
                {layoutMode === 'grid' && (
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-8 overflow-y-auto pr-4 custom-scrollbar">
                        {feeds.map((feed) => (
                            <div 
                                key={feed.feed_id} 
                                className="matrix-card p-0 group cursor-pointer hover:border-primary border-4 overflow-hidden"
                                onClick={() => handleFeedClick(feed.feed_id)}
                            >
                                <div className="bg-lcd-text text-lcd-bg px-2 py-0.5 text-[8px] font-bold uppercase flex justify-between group-hover:bg-primary group-hover:text-black">
                                    <span>{feed.name?.toUpperCase() || `NODE_${feed.feed_id.slice(-4)}`}</span>
                                    <span>{feed.status.toUpperCase()}</span>
                                </div>
                                <SurveillanceFeed 
                                    key={feed.feed_id}
                                    feed_id={feed.feed_id} 
                                    minimalControls={true} 
                                />
                                <div className="absolute inset-0 bg-transparent group-hover:bg-primary/5 pointer-events-none"></div>
                            </div>
                        ))}
                    </div>
                )}

                {layoutMode === 'focus' && focusedFeedId && (
                    <div className="flex h-full gap-8">
                        {/* Main Stage */}
                        <div className="flex-1 h-full matrix-card p-0 border-4 border-primary relative overflow-hidden group">
                            <div className="absolute top-0 left-0 right-0 z-10 bg-primary text-black px-3 py-1 text-[10px] font-bold uppercase tracking-widest flex justify-between items-center">
                                <span>CRITICAL_PATH_MONITOR: {focusedFeedId.toUpperCase()}</span>
                                <div className="flex items-center gap-2">
                                    <div className="h-2 w-2 rounded-full bg-black animate-pulse" />
                                    LIVE_FEED
                                </div>
                            </div>
                            {feeds.find(f => f.feed_id === focusedFeedId) && (
                                <SurveillanceFeed 
                                    key={focusedFeedId}
                                    feed_id={focusedFeedId} 
                                />
                            )}
                        </div>

                        {/* Thumbnails Sidebar */}
                        <div className="w-64 flex flex-col gap-4 overflow-y-auto pr-4 custom-scrollbar">
                            {feeds.map(feed => (
                                <div 
                                    key={feed.feed_id}
                                    className={cn(
                                        "matrix-card p-0 cursor-pointer border-4 group overflow-hidden",
                                        focusedFeedId === feed.feed_id 
                                            ? 'border-primary scale-105 shadow-xl' 
                                            : 'border-lcd-text/20 opacity-60 hover:opacity-100 hover:border-lcd-text/50'
                                    )}
                                    onClick={() => setFocusedFeedId(feed.feed_id)}
                                >
                                    <div className={cn(
                                        "px-2 py-0.5 text-[7px] font-bold uppercase flex justify-between",
                                        focusedFeedId === feed.feed_id ? "bg-primary text-black" : "bg-lcd-text/20 text-lcd-text"
                                    )}>
                                        <span className="truncate">{feed.name?.toUpperCase() || `NODE_${feed.feed_id.slice(-4)}`}</span>
                                    </div>
                                    <SurveillanceFeed 
                                        key={feed.feed_id}
                                        feed_id={feed.feed_id} 
                                        minimalControls={true} 
                                    />
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </div>
        )}
    </div>
  );
};

export default SurveillanceMatrix;