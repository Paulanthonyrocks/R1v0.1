"use client";

import React, { useState, useEffect } from 'react';
import { FeedStatusData } from '@/lib/types';
import SurveillanceFeed from './SurveillanceFeed';
import { Button } from '@/components/ui/button';
import { Grid, LayoutTemplate, Maximize2, Minimize2, VideoOff } from 'lucide-react';

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
    <div className="flex flex-col h-full w-full">
        {/* Matrix Toolbar */}
        <div className="flex justify-between items-center mb-4 p-2 bg-lcd-text/5 border border-lcd-text/20">
            <div className="flex items-center gap-4">
                <h3 className="text-sm font-bold uppercase tracking-widest font-lcd">
                    CAM_MATRIX_VIEW: <span className="text-primary">{layoutMode}</span>
                </h3>
                <div className="h-4 w-px bg-lcd-text/20"></div>
                <div className="flex gap-2">
                    <Button 
                        variant="ghost" 
                        size="sm" 
                        onClick={() => { setLayoutMode('grid'); setFocusedFeedId(null); }}
                        className={layoutMode === 'grid' ? 'bg-lcd-text text-lcd-bg' : ''}
                    >
                        <Grid size={16} />
                    </Button>
                    <Button 
                        variant="ghost" 
                        size="sm" 
                        onClick={() => { if (feeds.length > 0) { setLayoutMode('focus'); setFocusedFeedId(feeds[0].feed_id); }}}
                        className={layoutMode === 'focus' ? 'bg-lcd-text text-lcd-bg' : ''}
                        disabled={feeds.length === 0}
                    >
                        <Maximize2 size={16} />
                    </Button>
                </div>
            </div>
            
            <div className="text-[10px] opacity-60 font-mono">
                ACTIVE_STREAMS: {feeds.filter(f => f.status === 'running').length}/{feeds.length}
            </div>
        </div>

        {/* Matrix Content */}
        {feeds.length === 0 ? (
            <div className="flex-1 flex flex-col items-center justify-center border-2 border-dashed border-lcd-text/20 p-8 opacity-50">
                <VideoOff size={48} className="mb-4" />
                <p className="tracking-widest font-lcd">NO VIDEO FEEDS DETECTED</p>
                <p className="text-xs mt-2">Add feeds in the configuration panel</p>
            </div>
        ) : (
            <div className="flex-1 min-h-0 relative">
                {layoutMode === 'grid' && (
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 h-full overflow-y-auto pr-2 custom-scrollbar">
                        {feeds.map((feed) => (
                            <div 
                                key={feed.feed_id} 
                                className="aspect-video relative group cursor-pointer border-2 border-transparent hover:border-primary transition-all duration-100"
                                onClick={() => handleFeedClick(feed.feed_id)}
                            >
                                <SurveillanceFeed 
                                    feed={feed} 
                                    minimalControls={true} // We might want a 'minimal' prop in SurveillanceFeed to hide big controls
                                />
                                <div className="absolute inset-0 bg-transparent group-hover:bg-primary/5 pointer-events-none"></div>
                            </div>
                        ))}
                    </div>
                )}

                {layoutMode === 'focus' && focusedFeedId && (
                    <div className="flex h-full gap-4">
                        {/* Main Stage */}
                        <div className="flex-1 h-full border-2 border-primary relative">
                            {feeds.find(f => f.feed_id === focusedFeedId) && (
                                <SurveillanceFeed 
                                    feed={feeds.find(f => f.feed_id === focusedFeedId)!} 
                                />
                            )}
                        </div>

                        {/* Thumbnails Sidebar */}
                        <div className="w-48 flex flex-col gap-2 overflow-y-auto pr-2 custom-scrollbar">
                            {feeds.map(feed => (
                                <div 
                                    key={feed.feed_id}
                                    className={`aspect-video cursor-pointer border-2 transition-all ${focusedFeedId === feed.feed_id ? 'border-primary opacity-100' : 'border-lcd-text/20 opacity-60 hover:opacity-100'}`}
                                    onClick={() => setFocusedFeedId(feed.feed_id)}
                                >
                                    {/* Ideally we use a static snapshot or low-res stream here. 
                                        For now, reusing SurveillanceFeed but maybe we can disable rendering? 
                                        Or just show a placeholder/status if it's too heavy.
                                        Actually, let's just render it. React is efficient enough for a few. 
                                    */}
                                    <SurveillanceFeed feed={feed} minimalControls={true} />
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
