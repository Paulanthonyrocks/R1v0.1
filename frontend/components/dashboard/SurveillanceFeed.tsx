// components/dashboard/SurveillanceFeed.tsx
import React, { useState, useEffect } from 'react';
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Eye } from 'lucide-react';
import { cn } from "@/lib/utils";
import { SurveillanceFeedProps } from '@/lib/types'; // Import props type
import { useRealtimeUpdates } from '@/lib/hook'; // Import the hook

const SurveillanceFeed = React.memo(({ name, node, id }: SurveillanceFeedProps) => {
    const { feeds, sendMessage, isConnected } = useRealtimeUpdates();
    const feed = feeds.find(f => f.id === id);
    const [videoUrl, setVideoUrl] = useState<string | null>(null);

    useEffect(() => {
        // Set video URL based on feed status and source
        if (feed?.status === 'running' && feed.source) {
            // Assuming feed.source contains the stream URL or path
            // If it's just an identifier, you might need to construct the URL
            // e.g., setVideoUrl(`/api/stream/${feed.source}`);
            setVideoUrl(feed.source);
        } else {
            // Set to null if not running or no source provided
            setVideoUrl("/sample_traffic.mp4");
        }
    }, [feed]); // Re-run effect when feed data changes

    const toggleFeed = () => {
        if (!isConnected) {
            console.warn('WebSocket is not connected. Cannot toggle feed.');
            return;
        }
        if (!feed) {
            console.warn('Feed data not found. Cannot toggle feed.');
            return;
        }
        const newStatus = feed.status !== 'running';
        const messageType = newStatus ? 'start_feed' : 'stop_feed';
        sendMessage(messageType, { feed_id: id });
    };

    return (
        <Card
            className={cn(
                "matrix-glow-card overflow-hidden",
                "transition-transform duration-300 hover:scale-[1.03] cursor-pointer", // Hover effect and cursor
            )}
            onClick={toggleFeed} // Add click handler to the card
        >
            <div className="bg-black aspect-video flex items-center justify-center relative group">
                {videoUrl ? (
                    <video
                        src={videoUrl}
                        controls
                        autoPlay
                        loop
                        className="w-full h-full object-cover"
                    />
                ) : (
                    <div className="absolute inset-0 flex items-center justify-center opacity-30 group-hover:opacity-50 transition-opacity duration-300">
                        <Eye className="text-matrix-dark text-4xl" />
                    </div>
                )}
                {/* Status badge */}
                <Badge
                    variant={feed?.status === 'running' ? "default" : "outline"}
                    className={cn(
                        "absolute bottom-1.5 right-1.5 text-[10px] h-4 px-1.5",
                        feed?.status === 'running'
                            ? "bg-primary text-primary-foreground animate-matrix-pulse"
                            : "bg-muted text-muted-foreground",
                    )}
                >
                    {feed?.status === 'running' ? "LIVE" : "STOPPED"}
                </Badge>
            </div>
            <CardContent className="p-2">
                <h4 className="font-medium text-xs truncate text-foreground group-hover:text-matrix-light transition-colors">{name}</h4>
                <p className="text-[10px] text-muted-foreground truncate">{node}</p>
            </CardContent>
        </Card>
    );
});

SurveillanceFeed.displayName = 'SurveillanceFeed';
export default SurveillanceFeed;
