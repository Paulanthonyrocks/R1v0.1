import React, { useState, useEffect, useRef } from 'react';
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Eye, AlertTriangle, Loader2, RotateCw, Settings, Maximize, Minimize } from 'lucide-react';
import { cn } from "@/lib/utils";
import type { SurveillanceFeedProps } from '@/lib/types';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import useVideoSocket from '@/lib/useVideoSocket';
import useAuth from '@/lib/hook/useAuth';
import StreamOverlayControls from './StreamOverlayControls';

const SurveillanceFeed = React.memo(({ feed }: SurveillanceFeedProps) => {
    const { id, name: feedName, source, status } = feed;
    const { startFeed, stopFeed, isConnected: isSocketConnected, kpis } = useRealtimeUpdates();
    const { token } = useAuth();
    const { frameData, metrics, isConnected, error, drawFrame, frameRate: fps } = useVideoSocket(id, token);
    const [isToggling, setIsToggling] = useState<boolean>(false);
    const [showOverlays, setShowOverlays] = useState<boolean>(true);
    const [showBoundingBoxes, setShowBoundingBoxes] = useState<boolean>(true);
    const [showVehicleDetails, setShowVehicleDetails] = useState<boolean>(true);
    const [showControlsPanel, setShowControlsPanel] = useState<boolean>(false); // New state for panel visibility
    const [isFullScreen, setIsFullScreen] = useState<boolean>(false); // New state for fullscreen
    const cardRef = useRef<HTMLDivElement>(null); // Ref for the card element
    const canvasRef = useRef<HTMLCanvasElement>(null);

    const isLive = isConnected && frameData !== null;
    const isLoading = !isConnected && !error;

    const component_name = feedName ?? `Feed ${id}`;
    const component_node = `Source: ${source ?? 'N/A'}`;
    
    const toggleFullScreen = () => {
        if (cardRef.current) {
            if (!document.fullscreenElement) {
                cardRef.current.requestFullscreen().catch(err => {
                    console.error("Error attempting to enable fullscreen:", err);
                });
            } else {
                document.exitFullscreen();
            }
        }
    };


    useEffect(() => {
        if (status === 'running' || status === 'stopped' || status === 'error') {
            setIsToggling(false);
        }
    }, [status]);

    useEffect(() => {
        if (isLive && canvasRef.current && frameData) {
            const canvas = canvasRef.current;
            const ctx = canvas.getContext('2d');
            if (ctx) {
                if (isFullScreen) {
                    canvas.width = window.innerWidth;
                    canvas.height = window.innerHeight;
                } else {
                    canvas.width = canvas.offsetWidth;
                    canvas.height = canvas.offsetHeight;
                }
                drawFrame(ctx, frameData);
            }
        }
    }, [frameData, isLive, drawFrame, isFullScreen]);

    const handleStartFeed = () => {
        if (isToggling || !isSocketConnected) return;
        if (!id) {
            console.warn('Cannot start feed: feed ID is missing.');
            return;
        }
        setIsToggling(true);
        startFeed(id);
    };

    const handleStopFeed = () => {
        if (isToggling || !isSocketConnected) return;
        if (!id) {
            console.warn('Cannot stop feed: feed ID is missing.');
            return;
        }
        setIsToggling(true);
        stopFeed(id);
    };

    const handleRefreshFeed = () => {
        if (!isSocketConnected) {
            console.warn('Refresh prevented: Not connected to WebSocket');
            return;
        }
        console.log(`Sending refresh_feed for ${id}`);
        // sendMessage('refresh_feed', { feed_id: id });
    };

    return (
        <Card
            ref={cardRef} // Attach ref to the Card
            className={cn(
                "matrix-glow-card overflow-hidden group",
                "focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none",
                "font-lcd matrix-glow",
                isFullScreen ? "fixed inset-0 w-screen h-screen z-50 rounded-none" : "" // Fullscreen styles
            )}
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
                        {fps.toFixed(0)} FPS
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

                {metrics && isLive && !isToggling && !isLoading && !error && (
                    <div className="absolute top-1.5 right-1.5 text-xs text-lcd-bg group-hover:text-lcd-text bg-black/50 px-1.5 py-0.5 rounded-none backdrop-blur-sm tracking-normal font-lcd matrix-glow">
                        <span>VEH: {metrics.vehicle_count ?? '--'}</span>
                        <span>AVG SPEED: {metrics.avg_speed ? metrics.avg_speed.toFixed(1) : '--'} KM/H</span>
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
                {/* Fullscreen Button */}
                {!isToggling && !isLoading && !error && (
                    <button
                        className="absolute bottom-1.5 left-10 text-lcd-bg group-hover:text-lcd-text z-20 p-1 rounded-none bg-black/50 backdrop-blur-sm"
                        onClick={(e) => { e.stopPropagation(); toggleFullScreen(); }}
                        title={isFullScreen ? "Exit Fullscreen" : "Fullscreen"}
                    >
                        {isFullScreen ? <Minimize className="h-4 w-4" /> : <Maximize className="h-4 w-4" />}
                    </button>
                )}
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
                <div className="flex gap-2 mt-2">
                    <button
                        onClick={(e) => { e.stopPropagation(); handleStartFeed(); }}
                        disabled={isToggling || status === 'running' || status === 'starting'}
                        className="flex-1 px-2 py-1 text-xs font-semibold text-white bg-green-600 rounded-none disabled:bg-gray-500"
                    >
                        Start Feed
                    </button>
                    <button
                        onClick={(e) => { e.stopPropagation(); handleStopFeed(); }}
                        disabled={isToggling || status === 'stopped' || status === 'error'}
                        className="flex-1 px-2 py-1 text-xs font-semibold text-white bg-red-600 rounded-none disabled:bg-gray-500"
                    >
                        Stop Feed
                    </button>
                </div>
            </CardContent>
        </Card>
    );
});

SurveillanceFeed.displayName = 'SurveillanceFeed';
export default SurveillanceFeed;