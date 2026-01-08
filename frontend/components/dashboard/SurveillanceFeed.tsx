import React, { useState, useEffect, useRef, forwardRef } from 'react';
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Eye, AlertTriangle, Loader2, RotateCw, Settings, Play, Square, Maximize } from 'lucide-react';
import { cn } from "@/lib/utils";
import type { SurveillanceFeedProps } from '@/lib/types';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import useVideoSocket from '@/lib/useVideoSocket';
import useAuth from '@/lib/hook/useAuth';
import { UserRole } from '@/lib/auth/roles';
import StreamOverlayControls from './StreamOverlayControls';

const SurveillanceFeed = forwardRef<HTMLDivElement, SurveillanceFeedProps>(({ feed, minimalControls = false }, ref) => {
    const { feed_id, name: feedName, source, status } = feed;
    const { startFeed, stopFeed, restartFeed } = useRealtimeUpdates();
    const { token, userRole } = useAuth();
    
    // Only subscribe if the feed is in an active state
    const shouldSubscribe = status === 'running' || status === 'starting';
    const { lastFrameRef, metrics, isConnected, error, drawFrame, frameRate: fps, vehicles, updateFeedConfig } = useVideoSocket(
        shouldSubscribe ? feed_id : "", 
        token
    );
    
    const [isToggling, setIsToggling] = useState<boolean>(false);
    const [showOverlays, setShowOverlays] = useState<boolean>(true);
    const [showBoundingBoxes, setShowBoundingBoxes] = useState<boolean>(true);
    const [showVehicleDetails, setShowVehicleDetails] = useState<boolean>(true);
    const [showROI, setShowROI] = useState<boolean>(false);
    const [showControlsPanel, setShowControlsPanel] = useState<boolean>(false); 
    
    // ROI State
    const [roiMode, setRoiMode] = useState<boolean>(false);
    const [roiPoints, setRoiPoints] = useState<{x: number, y: number}[]>([]);

    const canvasRef = useRef<HTMLCanvasElement>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const toggleTimeoutRef = useRef<NodeJS.Timeout | null>(null);

    const isLive = isConnected && lastFrameRef.current !== null;
    const isLoading = !isConnected && !error;
    const isAdmin = userRole === UserRole.ADMIN;

    const component_name = feedName ?? `Feed ${feed_id}`;
    const component_node = `Source: ${source ?? 'N/A'}`;

    // Load existing ROI if available
    useEffect(() => {
        if (!roiMode && feed && feed.config && feed.config.roi) {
            setRoiPoints(feed.config.roi);
        }
    }, [feed, roiMode]);

    useEffect(() => {
        if (status === 'running' || status === 'stopped' || status === 'error') {
            setIsToggling(false);
            if (toggleTimeoutRef.current) {
                clearTimeout(toggleTimeoutRef.current);
                toggleTimeoutRef.current = null;
            }
        }
    }, [status]);

    const handleCanvasClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
        if (!roiMode || !canvasRef.current) return;

        const rect = canvasRef.current.getBoundingClientRect();
        const x = (e.clientX - rect.left) / rect.width;
        const y = (e.clientY - rect.top) / rect.height;

        setRoiPoints(prev => [...prev, { x, y }]);
    };

    const handleSaveROI = async () => {
        if (!feed_id) return;
        try {
            // Using WebSocket for configuration updates to avoid tunnel timeouts
            updateFeedConfig({ roi: roiPoints });
            setRoiMode(false);
            console.log("ROI Save command sent via WebSocket");
        } catch (error) {
            console.error("Error sending ROI update:", error);
        }
    };

    const handleClearROI = () => {
        setRoiPoints([]);
    };

    useEffect(() => {
        let animationFrameId: number;

        const render = () => {
            if (canvasRef.current) {
                const canvas = canvasRef.current;
                const ctx = canvas.getContext('2d', { alpha: false });
                if (ctx) {
                    const displayWidth = canvas.offsetWidth;
                    const displayHeight = canvas.offsetHeight;
                    
                    if (canvas.width !== displayWidth || canvas.height !== displayHeight) {
                        canvas.width = displayWidth;
                        canvas.height = displayHeight;
                    }
                    
                    // Draw Video
                    const currentFrame = lastFrameRef.current;
                    if (isLive && currentFrame && currentFrame.image) {
                        drawFrame(ctx, currentFrame, {
                            showBoundingBoxes: showOverlays && showBoundingBoxes,
                            showVehicleDetails: showOverlays && showVehicleDetails
                        });
                    } else {
                        // Clear canvas if not live
                        ctx.clearRect(0, 0, canvas.width, canvas.height);
                    }

                    // Draw ROI
                    if (roiMode || (showOverlays && showROI && roiPoints.length > 0)) {
                        ctx.save();
                        ctx.strokeStyle = roiMode ? '#00ff00' : 'rgba(0, 255, 0, 0.3)';
                        ctx.lineWidth = 1; // Thinner line
                        ctx.beginPath();
                        roiPoints.forEach((p, i) => {
                            const px = p.x * canvas.width;
                            const py = p.y * canvas.height;
                            if (i === 0) ctx.moveTo(px, py);
                            else ctx.lineTo(px, py);
                            
                            // Draw anchor points in edit mode
                            if (roiMode) {
                                ctx.fillStyle = '#00ff00';
                                ctx.fillRect(px - 2, py - 2, 4, 4); // Smaller anchors
                            }
                        });
                        if (roiPoints.length > 2) {
                            ctx.closePath();
                        }
                        ctx.stroke();
                        if (roiPoints.length > 2) {
                            ctx.fillStyle = 'rgba(0, 255, 0, 0.05)'; // More transparent fill
                            ctx.fill();
                        }
                        ctx.restore();
                    }
                }
            }
            animationFrameId = requestAnimationFrame(render);
        };

        render();

        return () => {
            if (animationFrameId) {
                cancelAnimationFrame(animationFrameId);
            }
        };
    }, [isLive, drawFrame, showOverlays, showBoundingBoxes, showVehicleDetails, showROI, feed_id, roiPoints, roiMode, lastFrameRef]);

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

    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
            if (showControlsPanel && containerRef.current && !containerRef.current.contains(event.target as Node)) {
                setShowControlsPanel(false);
            }
        };

        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, [showControlsPanel, containerRef]);

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'f' || e.key === 'F') {
            toggleFullScreen();
        }
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
                    <canvas 
                        ref={canvasRef} 
                        className={cn("w-full h-full object-cover image-rendering-pixelated filter-contrast-125", roiMode && "cursor-crosshair")}
                        width="640" 
                        height="480" 
                        onClick={handleCanvasClick}
                    />
                ) : (
                    <div className="absolute inset-0 flex items-center justify-center opacity-30">
                        <Eye className="text-lcd-bg group-hover:text-lcd-text text-4xl" />
                    </div>
                )}

                {/* ROI Controls */}
                {isAdmin && roiMode && (
                    <div className="absolute top-2 left-1/2 -translate-x-1/2 bg-black/80 p-2 rounded flex gap-2 z-50">
                        <span className="text-white text-xs self-center mr-2">Click to add points</span>
                        <button onClick={handleClearROI} className="px-2 py-1 bg-red-600 text-white text-xs rounded hover:bg-red-700">Clear</button>
                        <button onClick={handleSaveROI} className="px-2 py-1 bg-green-600 text-white text-xs rounded hover:bg-green-700">Save</button>
                        <button onClick={() => setRoiMode(false)} className="px-2 py-1 bg-gray-600 text-white text-xs rounded hover:bg-gray-700">Cancel</button>
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

                {metrics && isLive && !isToggling && !isLoading && !error && !minimalControls && (
                    <div className="absolute top-1.5 right-1.5 text-xs text-lcd-bg group-hover:text-lcd-text bg-black/50 px-1.5 py-0.5 rounded-none backdrop-blur-sm tracking-normal font-lcd matrix-glow flex flex-col items-end z-20">
                        <span>VEH: {metrics.total_vehicles_cumulative ?? metrics.total_vehicles ?? '--'}</span>
                        <span>AVG SPEED: {metrics.session_average_speed_kmh ? metrics.session_average_speed_kmh.toFixed(1) : (metrics.average_speed_kmh ? metrics.average_speed_kmh.toFixed(1) : '--')} KM/H</span>
                    </div>
                )}

                {!isToggling && !isLoading && !minimalControls && (
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
                        {isAdmin && (
                            <button
                                onClick={(e) => { e.stopPropagation(); setRoiMode(!roiMode); }}
                                title="Set Region of Interest"
                                className={cn("p-1 text-lcd-bg group-hover:text-lcd-text bg-black/50 backdrop-blur-sm rounded-none hover:bg-black/70", roiMode && "bg-primary text-primary-foreground")}
                            >
                                <Square className="h-4 w-4" style={{ transform: 'rotate(45deg)' }} />
                            </button>
                        )}
                    </div>
                )}

                {!isToggling && !isLoading && !error && !minimalControls && (
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
                            showROI={showROI}
                            setShowROI={setShowROI}
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
