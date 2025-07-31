import React, { useState, useEffect, useRef } from 'react';
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Eye, AlertTriangle, Loader2, RotateCw, Settings } from 'lucide-react';
import { cn } from "@/lib/utils";
import type { SurveillanceFeedProps } from '@/lib/types';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import useVideoStream from '@/lib/useVideoStream';
import StreamOverlayControls from './StreamOverlayControls'; // Import the new component

const SurveillanceFeed = React.memo(({ feed }: SurveillanceFeedProps) => {
    const { id, name: feedName, source, status } = feed;
    const { videoUrl, isLoading, error, isLive, kpis, canvasRef, frameRate: fps } = useVideoStream({
        streamId: id,
        forceSample: false,
        streamType: 'websocket'
    });
    const component_name = feedName ?? `Feed ${id}`;
    const component_node = `Source: ${source ?? 'N/A'}`;

    
    const { sendMessage, isConnected } = useRealtimeUpdates();
    

    const [isToggling, setIsToggling] = useState<boolean>(false);
    const [showOverlays, setShowOverlays] = useState<boolean>(true);
    const [showBoundingBoxes, setShowBoundingBoxes] = useState<boolean>(true);
    const [showVehicleDetails, setShowVehicleDetails] = useState<boolean>(true);
    const [showControlsPanel, setShowControlsPanel] = useState<boolean>(false); // New state for panel visibility

    useEffect(() => {
        if (status === 'running' || status === 'stopped' || status === 'error') {
            setIsToggling(false);
        }
    }, [status]);

    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas) return;

        const context = canvas.getContext('2d');
        if (!context) return;

        // Clear canvas before drawing new boxes
        context.clearRect(0, 0, canvas.width, canvas.height);

        if (showOverlays && kpis && kpis.vehicles) {
            context.font = '10px monospace';
            context.fillStyle = '#00FF00'; // Green color for text

            kpis.vehicles.forEach((vehicle: { x1: number; y1: number; x2: number; y2: number; id: string; speed: number; }) => {
                if (showBoundingBoxes) {
                    context.strokeStyle = '#FF0000'; // Red color for boxes
                    context.lineWidth = 2;
                    context.strokeRect(vehicle.x1, vehicle.y1, vehicle.x2 - vehicle.x1, vehicle.y2 - vehicle.y1);
                }

                if (showVehicleDetails) {
                    context.fillText(`ID: ${vehicle.id}`, vehicle.x1, vehicle.y1 - 10);
                    context.fillText(`SPD: ${vehicle.speed} KM/H`, vehicle.x1, vehicle.y1 - 25);
                }
            });
        }
    }, [kpis, showOverlays, showBoundingBoxes, showVehicleDetails]);

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
                "matrix-glow-card overflow-hidden group",
                "cursor-pointer",
                "focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none",
                "font-lcd matrix-glow"
            )}
            onClick={isToggling ? undefined : toggleFeed}
            tabIndex={0}
        >
            <div className="bg-black aspect-video flex items-center justify-center relative group overflow-hidden">
                {isLive ? (
                    <canvas ref={canvasRef} className="w-full h-full object-cover image-rendering-pixelated filter-contrast-125" width="640" height="480" />
                ) : (
                    <div className="absolute inset-0 flex items-center justify-center opacity-30">
                        <Eye className="text-lcd-bg group-hover:text-lcd-text text-4xl" />
                    </div>
                )}

                {(isToggling || isLoading) && (
                    <div className="absolute inset-0 flex items-center justify-center bg-black/50 z-10 rounded-none">
                        <Loader2 className="text-lcd-bg group-hover:text-lcd-text animate-spin h-10 w-10" />
                    </div>
                )}

                {error && !isToggling && (
                    <div className="absolute inset-0 flex flex-col items-center justify-center opacity-80 p-2 bg-lcd-text rounded-none">
                        <AlertTriangle className="text-lcd-bg group-hover:text-lcd-text text-3xl mb-1" />
                        <p className="text-lcd-bg group-hover:text-lcd-text text-center tracking-normal font-lcd matrix-glow">VIDEO FEED UNAVAILABLE</p>
                    </div>
                )}

                {isLive && typeof fps === 'number' && !isToggling && !isLoading && !error && (
                    <Badge variant="outline" className="absolute top-1.5 left-1.5 text-[10px] h-4 px-1.5 bg-black/50 text-primary-foreground/80 group-hover:text-lcd-text backdrop-blur-sm tracking-normal rounded-none font-lcd matrix-glow">
                        {fps}
                    </Badge>
                )}
                <Badge
                    variant={isLive ? "default" : "outline"}
                    className={cn(
                        "absolute bottom-1.5 right-1.5 text-[10px] h-4 px-1.5 tracking-normal rounded-none font-lcd matrix-glow",
                        isLive
                            ? "bg-primary text-primary-foreground animate-matrix-pulse"
                            : "bg-lcd-text text-lcd-bg",
                    )}
                >
                    {isLive ? "LIVE" : status?.toUpperCase() ?? "UNKNOWN"}
                </Badge>

                {kpis && isLive && !isToggling && !isLoading && !error && (
                    <div className="absolute top-1.5 right-1.5 text-xs text-lcd-bg group-hover:text-lcd-text bg-black/50 px-1.5 py-0.5 rounded-none backdrop-blur-sm tracking-normal font-lcd matrix-glow">
                        <span>VEH: {kpis.vehicle_count ?? '--'}</span>
                        <span>AVG SPEED: {kpis.avg_speed ?? '--'} KM/H</span>
                    </div>
                )}
                {!isToggling && !isLoading && (
                    <button
                        className="absolute bottom-1.5 left-1.5 text-lcd-bg group-hover:text-lcd-text z-20 p-1 rounded-none bg-black/50 backdrop-blur-sm"
                        onClick={(e) => { e.stopPropagation(); handleRefreshFeed(); }}
                        title="Refresh Feed"
                    >
                        <RotateCw className="h-4 w-4" />
                    </button>)
                }
                {/* New: Overlay Controls Button */}
                {!isToggling && !isLoading && !error && (
                    <button
                        className="absolute top-1.5 right-1.5 text-lcd-bg group-hover:text-lcd-text z-20 p-1 rounded-none bg-black/50 backdrop-blur-sm"
                        onClick={(e) => { e.stopPropagation(); setShowControlsPanel(!showControlsPanel); }}
                        title="Overlay Controls"
                    >
                        <Settings className="h-4 w-4" />
                    </button>
                )}

                {/* New: Overlay Controls Panel */}
                {showControlsPanel && (
                    <div className="absolute top-10 right-1.5 z-30">
                        <StreamOverlayControls
                            showOverlays={showOverlays}
                            setShowOverlays={setShowOverlays}
                            showBoundingBoxes={showBoundingBoxes}
                            setShowBoundingBoxes={setShowBoundingBoxes}
                            showVehicleDetails={showVehicleDetails}
                            setShowVehicleDetails={setShowVehicleDetails}
                        />
                    </div>
                )}
            </div>
            <CardContent className="p-2 rounded-none">
                <h4 className="font-medium text-xs truncate text-lcd-bg group-hover:text-lcd-text transition-colors tracking-normal font-lcd matrix-glow">{component_name}</h4>
                <p className="text-[10px] text-lcd-bg group-hover:text-lcd-text transition-colors truncate tracking-normal font-lcd matrix-glow">{component_node}</p>
            </CardContent>
        </Card>
    );
});

SurveillanceFeed.displayName = 'SurveillanceFeed';
export default SurveillanceFeed;
