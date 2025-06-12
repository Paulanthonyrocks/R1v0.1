// components/dashboard/SurveillanceFeed.tsx
import React, { useState, useEffect } from 'react';
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Eye, AlertTriangle, Loader2 } from 'lucide-react';
import { cn } from "@/lib/utils";
import type { SurveillanceFeedProps } from '@/lib/types'; // Will point to updated definition
import { useRealtimeUpdates } from '@/lib/hook';

const SurveillanceFeed = React.memo(({ feed }: SurveillanceFeedProps) => {
    const { id, name: feedName, source, status, fps } = feed; // Destructure from the feed prop
    const component_name = feedName ?? `Feed ${id}`; // Renamed to avoid conflict with 'name' prop if it existed
    const component_node = `Source: ${source ?? 'N/A'}`; // Renamed to avoid conflict

    const { sendMessage, isConnected } = useRealtimeUpdates();

    const [videoUrl, setVideoUrl] = useState<string | null>(null);
    const [videoErrorOccurred, setVideoErrorOccurred] = useState<boolean>(false);
    const [isToggling, setIsToggling] = useState<boolean>(false);

    useEffect(() => {
        setVideoErrorOccurred(false);
        if (status === 'running' && source) {
            setVideoUrl(source);
        } else {
            setVideoUrl(null);
        }
    }, [status, source]);

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
            <div className="bg-black aspect-video flex items-center justify-center relative group">
                {isToggling && (
                    <div className="absolute inset-0 flex items-center justify-center bg-black/50 z-10">
                        <Loader2 className="text-primary-foreground animate-spin h-10 w-10" /> {/* Changed text-white to text-primary-foreground */}
                    </div>
                )}
                {videoUrl && !videoErrorOccurred && !isToggling ? (
                    <video
                        key={videoUrl}
                        src={videoUrl}
                        controls
                        muted
                        autoPlay={status === 'running'}
                        loop={status === 'running'}
                        className="w-full h-full object-cover"
                        onError={() => {
                            console.warn(`Error loading video: ${videoUrl} for feed ID: ${id}`);
                            setVideoErrorOccurred(true);
                        }}
                        onCanPlay={() => {
                            if (videoErrorOccurred) {
                                setVideoErrorOccurred(false);
                            }
                        }}
                    />
                ) : videoErrorOccurred && !isToggling ? (
                    <div className="absolute inset-0 flex flex-col items-center justify-center opacity-80 p-2 bg-card">
                        <AlertTriangle className="text-primary text-3xl mb-1" /> {/* Changed text-destructive to text-primary */}
                        <p className="text-xs text-primary text-center tracking-normal">Video feed unavailable</p> {/* Changed text-destructive to text-primary, added tracking-normal */}
                    </div>
                ) : !isToggling ? (
                    <div className="absolute inset-0 flex items-center justify-center opacity-30 group-hover:opacity-50 transition-opacity duration-300">
                        <Eye className="text-primary-foreground text-4xl" /> {/* Changed to green for visibility on black bg */}
                    </div>
                ) : null}
                {status === 'running' && typeof fps === 'number' && !isToggling && (
                  <Badge variant="outline" className="absolute top-1.5 left-1.5 text-[10px] h-4 px-1.5 bg-black/50 text-primary-foreground backdrop-blur-sm tracking-normal"> {/* Changed text-white, added tracking-normal */}
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
            </div>
            <CardContent className="p-2">
                <h4 className="font-medium text-xs truncate text-foreground group-hover:text-matrix-light transition-colors tracking-normal">{component_name}</h4> {/* Added tracking-normal */}
                <p className="text-[10px] text-muted-foreground truncate tracking-normal">{component_node}</p> {/* Added tracking-normal */}
            </CardContent>
        </Card>
    );
});

SurveillanceFeed.displayName = 'SurveillanceFeed';
export default SurveillanceFeed;
