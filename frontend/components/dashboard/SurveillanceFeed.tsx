import React, { useState, useEffect, useRef, forwardRef } from 'react';
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Eye, AlertTriangle, Loader2, RotateCw, Settings, Play, Square, Maximize } from 'lucide-react';
import { cn } from "@/lib/utils";
import type { SurveillanceFeedProps } from '@/lib/types';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import useVideoSocket from '@/lib/useVideoSocket';
import useAuth from '@/lib/hook/useAuth';
import StreamOverlayControls from './StreamOverlayControls';

const SurveillanceFeed = forwardRef<HTMLDivElement, SurveillanceFeedProps>(({ feed }, ref) => {
    const { feed_id, name: feedName, source, status } = feed;
    const { startFeed, stopFeed, restartFeed, isConnected: isSocketConnected, /*kpis*/ } = useRealtimeUpdates();
    const { token } = useAuth();
    const { frameData, metrics, isConnected, error, drawFrame, frameRate: fps, vehicles } = useVideoSocket(feed_id, token);
    const [isToggling, setIsToggling] = useState<boolean>(false);
    const [showOverlays, setShowOverlays] = useState<boolean>(true);
    const [showBoundingBoxes, setShowBoundingBoxes] = useState<boolean>(true);
    const [showVehicleDetails, setShowVehicleDetails] = useState<boolean>(true);
    const [showControlsPanel, setShowControlsPanel] = useState<boolean>(false); 
    const canvasRef = useRef<HTMLCanvasElement>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const toggleTimeoutRef = useRef<NodeJS.Timeout | null>(null);

    const isLive = isConnected && frameData !== null;
    const isLoading = !isConnected && !error;

    const component_name = feedName ?? `Feed ${feed_id}`;
    const component_node = `Source: ${source ?? 'N/A'}`;

    // Clear timeout on unmount
    useEffect(() => {
        return () => {
            if (toggleTimeoutRef.current) clearTimeout(toggleTimeoutRef.current);
        };
    }, []);
    
    useEffect(() => {
        if (status === 'running' || status === 'stopped' || status === 'error') {
            setIsToggling(false);
            if (toggleTimeoutRef.current) {
                clearTimeout(toggleTimeoutRef.current);
                toggleTimeoutRef.current = null;
            }
        }
    }, [status]);

    useEffect(() => {
        if (isLive && canvasRef.current && frameData) {
            const canvas = canvasRef.current;
            const ctx = canvas.getContext('2d', { alpha: false }); // alpha: false optimizes rendering if no transparency needed
            if (ctx) {
                // OPTIMIZATION: Only resize if dimensions mismatch
                const displayWidth = canvas.offsetWidth;
                const displayHeight = canvas.offsetHeight;
                
                if (canvas.width !== displayWidth || canvas.height !== displayHeight) {
                    canvas.width = displayWidth;
                    canvas.height = displayHeight;
                }
                ctx.clearRect(0, 0, canvas.width, canvas.height);
                drawFrame(ctx, frameData, vehicles, {
                    showBoundingBoxes: showOverlays && showBoundingBoxes,
                    showVehicleDetails: showOverlays && showVehicleDetails
                });
            }
        }
    }, [frameData, isLive, drawFrame, showOverlays, showBoundingBoxes, showVehicleDetails, vehicles]);

    const handleStartFeed = () => {
        if (isToggling) return;
        if (!feed_id) {
            console.warn('Cannot start feed: feed ID is missing.');
            return;
        }
        setIsToggling(true);
        startFeed(feed_id);
        
        if (toggleTimeoutRef.current) clearTimeout(toggleTimeoutRef.current);
        toggleTimeoutRef.current = setTimeout(() => setIsToggling(false), 5000);
    };

    const handleStopFeed = () => {
        if (isToggling) return;
        if (!feed_id) {
            console.warn('Cannot stop feed: feed ID is missing.');
            return;
        }
        setIsToggling(true);
        stopFeed(feed_id);

        if (toggleTimeoutRef.current) clearTimeout(toggleTimeoutRef.current);
        toggleTimeoutRef.current = setTimeout(() => setIsToggling(false), 5000);
    };

    const handleRestartFeed = () => {
        if (!feed_id) {
            console.warn('Cannot restart feed: feed ID is missing.');
            return;
        }
        setIsToggling(true);
        console.log(`Sending restart_feed for ${feed_id}`);
        restartFeed(feed_id);

        if (toggleTimeoutRef.current) clearTimeout(toggleTimeoutRef.current);
        toggleTimeoutRef.current = setTimeout(() => setIsToggling(false), 5000);
    };

    const toggleFullScreen = () => {
        if (containerRef.current) {
            if (!document.fullscreenElement) {
                containerRef.current.requestFullscreen().catch(err => {
                    console.error(`Error attempting to enable full-screen mode: ${err.message} (${err.name})`);
                });
            } else {
                document.exitFullscreen();
            }
        }
    };

    // Effect to handle clicking outside the controls panel
    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (showControlsPanel && containerRef.current && !containerRef.current.contains(event.target as Node)) {
                setShowControlsPanel(false);
            }
        };

        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, [showControlsPanel, containerRef]); // Add containerRef to dependencies

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'f' || e.key === 'F') {
            toggleFullScreen();
        }
        // Add other shortcuts as needed
    };

    return (
        <Card
            ref={ref} 
            onKeyDown={handleKeyDown}
            className={cn(
                "matrix-glow-card overflow-hidden group",
                "focus-visible:ring-2 focus-visible:ring-primary focus-visible:outline-none",
                "font-lcd matrix-glow",
            )}
            tabIndex={0}
        >
            <div ref={containerRef} className="bg-black aspect-video flex items-center justify-center relative group overflow-hidden">
                {isLive ? (
                    <canvas ref={canvasRef} className="w-full h-full object-cover image-rendering-pixelated filter-contrast-125" width="640" height="480" />
                ) : (
                    <div className="absolute inset-0 flex items-center justify-center opacity-30">
                        <Eye className="text-lcd-bg group-hover:text-lcd-text text-4xl" />
                    </div>
                )}

                {(isToggling || isLoading) && !isLive && (
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

                {/* FPS Badge - Top Left */}
                {isLive && typeof fps === 'number' && !isToggling && !isLoading && !error && (
                    <Badge variant="outline" className="absolute top-1.5 left-1.5 text-[10px] h-4 px-1.5 bg-black/50 text-primary-foreground/80 group-hover:text-lcd-text backdrop-blur-sm tracking-normal rounded-none font-lcd matrix-glow">
                        {fps.toFixed(0)} FPS
                    </Badge>
                )}

                {/* Status Badge - Bottom Right */}
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

                {/* Metrics Display - Top Right */}
                {metrics && isLive && !isToggling && !isLoading && !error && (
                    <div className="absolute top-1.5 right-1.5 text-xs text-lcd-bg group-hover:text-lcd-text bg-black/50 px-1.5 py-0.5 rounded-none backdrop-blur-sm tracking-normal font-lcd matrix-glow flex flex-col items-end z-20">
                        <span>VEH: {metrics.total_vehicles_cumulative ?? metrics.total_vehicles ?? '--'}</span>
                        <span>AVG SPEED: {metrics.session_average_speed_kmh ? metrics.session_average_speed_kmh.toFixed(1) : (metrics.average_speed_kmh ? metrics.average_speed_kmh.toFixed(1) : '--')} KM/H</span>
                    </div>
                )}

                {/* Playback Controls - Bottom Left */}
                {!isToggling && !isLoading && (
                    <div className="absolute bottom-1.5 left-1.5 flex gap-2 z-20 opacity-0 group-hover:opacity-100 transition-opacity duration-300">
                        <button
                            onClick={(e) => { e.stopPropagation(); handleRestartFeed(); }}
                            title="Restart Feed"
                            className="p-1 text-lcd-bg group-hover:text-lcd-text bg-black/50 backdrop-blur-sm rounded-none hover:bg-black/70"
                        >
                            <RotateCw className="h-4 w-4" />
                        </button>
                        <button
                            onClick={(e) => { e.stopPropagation(); handleStartFeed(); }}
                            disabled={isToggling || status === 'running' || status === 'starting'}
                            title="Start Feed"
                            className="p-1 text-lcd-bg group-hover:text-lcd-text bg-black/50 backdrop-blur-sm rounded-none hover:bg-black/70 disabled:opacity-50"
                        >
                            <Play className="h-4 w-4" />
                        </button>
                        <button
                            onClick={(e) => { e.stopPropagation(); handleStopFeed(); }}
                            disabled={isToggling || status === 'stopped' || status === 'error'}
                            title="Stop Feed"
                            className="p-1 text-lcd-bg group-hover:text-lcd-text bg-black/50 backdrop-blur-sm rounded-none hover:bg-black/70 disabled:opacity-50"
                        >
                            <Square className="h-4 w-4" />
                        </button>
                    </div>
                )}

                {/* Tools (Settings & Fullscreen) - Below Metrics (Top Right Area) */}
                {!isToggling && !isLoading && !error && (
                    <div className="absolute top-12 right-1.5 flex flex-col gap-2 z-20 opacity-0 group-hover:opacity-100 transition-opacity duration-300">
                        <button
                            className="text-lcd-bg group-hover:text-lcd-text p-1 rounded-none bg-black/50 backdrop-blur-sm hover:bg-black/70"
                            onClick={(e) => { e.stopPropagation(); setShowControlsPanel(!showControlsPanel); }}
                            title="Overlay Controls"
                        >
                            <Settings className="h-4 w-4" />
                        </button>
                        <button
                            className="text-lcd-bg group-hover:text-lcd-text p-1 rounded-none bg-black/50 backdrop-blur-sm hover:bg-black/70"
                            onClick={(e) => { e.stopPropagation(); toggleFullScreen(); }}
                            title="Fullscreen"
                        >
                            <Maximize className="h-4 w-4" />
                        </button>
                    </div>
                )}
                
                {showControlsPanel && (
                    <div className="absolute top-12 right-8 z-30">
                        <StreamOverlayControls
                            showOverlays={showOverlays}
                            setShowOverlays={setShowOverlays}
                            showBoundingBoxes={showBoundingBoxes}
                            setShowBoundingBoxes={setShowBoundingBoxes}
                            showVehicleDetails={showVehicleDetails}
                            setShowVehicleDetails={setShowVehicleDetails}
                            controlId={feed_id}
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
