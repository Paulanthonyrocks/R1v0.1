// components/dashboard/SurveillanceFeed.tsx
import React, { useState, useEffect } from 'react';
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Eye, AlertTriangle, Loader2 } from 'lucide-react';
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

    return (
        <Card
            className={cn(
                "matrix-glow-card overflow-hidden",
                "cursor-pointer",
                "focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none" // Added focus state
            )}
            onClick={isToggling ? undefined : toggleFeed}
            tabIndex={0} // Make it focusable
        >
            {/* Wrap the main content in a single parent div */}
            <div className="bg-black aspect-video flex items-center justify-center relative group overflow-hidden"> {/* Added overflow-hidden */}
                {/* Display the video image or placeholder */}
                {image ? ( 
                    <img src={image} alt={`${feedName} Feed`} className="w-full h-full object-cover" />
                ) : (
                     <div className="absolute inset-0 flex items-center justify-center opacity-30">
                         <Eye className="text-primary-foreground text-4xl" />
                     </div>
                )}

                {/* Loading state overlay */}
                {(isToggling || isStreamLoading) && (
                    <div className="absolute inset-0 flex items-center justify-center bg-black/50 z-10">
                        <Loader2 className="text-primary-foreground animate-spin h-10 w-10" /> {/* Changed text-white to text-primary-foreground */}
                    </div> // Corrected closing tag
                )}

                {/* Stream error overlay */}
                {streamError && !isToggling && (
                    <div className="absolute inset-0 flex flex-col items-center justify-center opacity-80 p-2 bg-card">
                        <AlertTriangle className="text-primary text-3xl mb-1" /> {/* Changed text-destructive to text-primary */}
                        <p className="text-xs text-primary text-center tracking-normal">Video feed unavailable</p> {/* Changed text-destructive to text-primary, added tracking-normal */}
                    </div>
                )}

                {/* FPS Badge */}
                {status === 'running' && typeof fps === 'number' && !isToggling && !isStreamLoading && !streamError && (
                  <Badge variant="outline" className="absolute top-1.5 left-1.5 text-[10px] h-4 px-1.5 bg-black/50 text-primary-foreground/80 backdrop-blur-sm tracking-normal"> {/* Changed text-white, added tracking-normal, reduced opacity slightly */}
                    {fps} FPS
                  </Badge>
                )}
                <Badge
                    variant={status === 'running' ? "default" : "outline"}
                    className={cn(
                        "absolute bottom-1.5 right-1.5 text-[10px] h-4 px-1.5 tracking-normal", // Added tracking-normal
                        status === 'running'
                            ? "bg-primary text-primary-foreground animate-matrix-pulse"
                            : "bg-card text-primary", // Changed non-LIVE status badge style
                    )}
                >
                    {status === 'running' ? "LIVE" : status?.toUpperCase() ?? "UNKNOWN"}
                </Badge>

                {/* Metrics Overlay */}
                 {metrics && status === 'running' && !isToggling && !isStreamLoading && !streamError && (
                     <div className="absolute top-1.5 right-1.5 text-xs text-primary-foreground/80 bg-black/50 px-1.5 py-0.5 rounded backdrop-blur-sm tracking-normal flex flex-col items-end">
                        <span>Veh: {metrics.total_vehicles ?? '--'}</span>
                        <span>Avg Speed: {metrics.average_speed_kmh ?? '--'} km/h</span>
                     </div>
                 )}
            </div>
            <CardContent className="p-2">
                <h4 className="font-medium text-xs truncate text-foreground group-hover:text-matrix-light transition-colors tracking-normal">{component_name}</h4> {/* Added tracking-normal */}
                <p className="text-[10px] text-muted-foreground truncate tracking-normal">{component_node}</p> {/* Added tracking-normal */}
            </CardContent>
        </Card>
    );
}); // Corrected closing parenthesis and semicolon

SurveillanceFeed.displayName = 'SurveillanceFeed';
export default SurveillanceFeed;
