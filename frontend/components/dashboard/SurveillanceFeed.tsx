// components/dashboard/SurveillanceFeed.tsx
import React, { useState, useEffect } from 'react';
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Eye, AlertTriangle, Loader2, RotateCw } from 'lucide-react';
import { cn } from "@/lib/utils";
import type { SurveillanceFeedProps, TrafficMetrics } from '@/lib/types';
import { useRealtimeUpdates } from '@/lib/hook'; // Assuming useRealtimeUpdates is still needed from this hook file
import useMultipartStream from '@/lib/useMultipartStream';

const SurveillanceFeed = React.memo(({ feed }: SurveillanceFeedProps) => {
    const { id, name: feedName, source, status, fps } = feed; // Destructure from the feed prop
    const component_name = feedName ?? `Feed ${id}`; // Renamed to avoid conflict with 'name' prop if it existed
    const component_node = `Source: ${source ?? 'N/A'}`; // Renamed to avoid conflict

    const { sendMessage, isConnected } = useRealtimeUpdates();

    const streamUrl = status === 'running' && source ? source : null;
    const { image, metrics, error: streamError, isLoading: isStreamLoading } = useMultipartStream(streamUrl);

    const [isToggling, setIsToggling] = useState<boolean>(false); // State for toggle button loading

    useEffect(() => { 
 if (status === 'running' || status === 'stopped' || status === 'error') {
            setIsToggling(false);
        } else if (status === 'starting' || status === 'stopping') {
            setIsToggling(true);
        }
    }, [status]);

    const toggleFeed = () => {
        if (isToggling || !isConnected) {
            console.warn('Toggle prevented:', { isToggling, isConnected, hasFeed: !!feed });
            return;
        }
        setIsToggling(true);

        const newTargetStatusRunning = status !== 'running' && status !== 'starting';
        const messageType = newTargetStatusRunning ? 'start_feed' : 'stop_feed';
        sendMessage(messageType, { feed_id: id });
    };

    const handleRefreshFeed = () => {
        if (!isConnected) {
            console.warn('Refresh prevented: Not connected to WebSocket');
            return;
        }
        console.log(`Sending refresh_feed for ${id}`);
        sendMessage('refresh_feed', { feed_id: id });
    };
    
    return (
        <Card
            className={cn(
                "matrix-glow-card overflow-hidden group", // Added 'group' class here
                "cursor-pointer",
                "focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none", // Added focus state
                "font-lcd matrix-glow"
            )}
            onClick={isToggling ? undefined : toggleFeed}
            tabIndex={0}
        >
            <div className="bg-black aspect-video flex items-center justify-center relative group overflow-hidden"> {/* Added overflow-hidden */}
                {/* Display the video image or placeholder */}
                {image ? ( 
                    <img src={image} alt={`${feedName} Feed`} className="w-full h-full object-cover image-rendering-pixelated filter-contrast-125" />
                ) : (
                     <div className="absolute inset-0 flex items-center justify-center opacity-30">
                         <Eye className="text-lcd-bg group-hover:text-lcd-text text-4xl" />
                     </div>
                )}

                {/* Loading state overlay */}
                {(isToggling || isStreamLoading) && (
                    <div className="absolute inset-0 flex items-center justify-center bg-black/50 z-10 rounded-none">
                        <Loader2 className="text-lcd-bg group-hover:text-lcd-text animate-spin h-10 w-10" />
                    </div> // Corrected closing tag
                )}

                {/* Stream error overlay */}
                {streamError && !isToggling && (
                    <div className="absolute inset-0 flex flex-col items-center justify-center opacity-80 p-2 bg-lcd-text rounded-none">
                        <AlertTriangle className="text-lcd-bg group-hover:text-lcd-text text-3xl mb-1" />
                        <p className="text-lcd-bg group-hover:text-lcd-text text-center tracking-normal font-lcd matrix-glow">VIDEO FEED UNAVAILABLE</p>
                    </div>
                )}

                {/* FPS Badge */}
                {status === 'running' && typeof fps === 'number' && !isToggling && !isStreamLoading && !streamError && (
                  <Badge variant="outline" className="absolute top-1.5 left-1.5 text-[10px] h-4 px-1.5 bg-black/50 text-primary-foreground/80 group-hover:text-lcd-text backdrop-blur-sm tracking-normal rounded-none font-lcd matrix-glow"> {/* Changed text-white, added tracking-normal, reduced opacity slightly, removed rounded, font-lcd */}
                    {fps}
                  </Badge>
                )}
                <Badge
                    variant={status === 'running' ? "default" : "outline"}
                    className={cn(
                        "absolute bottom-1.5 right-1.5 text-[10px] h-4 px-1.5 tracking-normal rounded-none font-lcd matrix-glow",
                        status === 'running'
                            ? "bg-primary text-primary-foreground animate-matrix-pulse"
                            : "bg-lcd-text text-lcd-bg", // Changed non-LIVE status badge style
                    )}
                >
                    {status === 'running' ? "LIVE" : status?.toUpperCase() ?? "UNKNOWN"}
                </Badge>

                {/* Metrics Overlay */}
                 {metrics && status === 'running' && !isToggling && !isStreamLoading && !streamError && (
                     <div className="absolute top-1.5 right-1.5 text-xs text-lcd-bg group-hover:text-lcd-text bg-black/50 px-1.5 py-0.5 rounded-none backdrop-blur-sm tracking-normal font-lcd matrix-glow">
                        <span>VEH: {metrics.total_vehicles ?? '--'}</span>
                        <span>AVG SPEED: {metrics.average_speed_kmh ?? '--'} KM/H</span>
                     </div>
                 )}
                 {/* Refresh Button */}
                {!isToggling && !isStreamLoading && ( // Only show when not toggling or loading
                    <button
                        className="absolute bottom-1.5 left-1.5 text-lcd-bg group-hover:text-lcd-text z-20 p-1 rounded-none bg-black/50 backdrop-blur-sm"
                        onClick={(e) => { e.stopPropagation(); handleRefreshFeed(); }} // Stop click event from propagating to card toggle
                    >
                        <RotateCw size={14} />
                    </button>)}
            </div>
            <CardContent className="p-2 rounded-none">
                <h4 className="font-medium text-xs truncate text-lcd-bg group-hover:text-lcd-text transition-colors tracking-normal font-lcd matrix-glow">{component_name}</h4>
                <p className="text-[10px] text-lcd-bg group-hover:text-lcd-text transition-colors truncate tracking-normal font-lcd matrix-glow">{component_node}</p>
            </CardContent>
        </Card>
    );
}); // Corrected closing parenthesis and semicolon

SurveillanceFeed.displayName = 'SurveillanceFeed';
export default SurveillanceFeed;
