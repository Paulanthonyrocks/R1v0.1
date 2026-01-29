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
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

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
    const [showTrajectories, setShowTrajectories] = useState<boolean>(true);
    const [showROI, setShowROI] = useState<boolean>(false);
    const [showExclusionZones, setShowExclusionZones] = useState<boolean>(true);
    const [showControlsPanel, setShowControlsPanel] = useState<boolean>(false);
    const [staticFilterEnabled, setStaticFilterEnabled] = useState<boolean>(feed.config?.static_object_filter_enabled ?? false);

    // ROI & Exclusion Zone State
    const [roiMode, setRoiMode] = useState<'roi' | 'exclusion' | null>(null);
    const [roiPoints, setRoiPoints] = useState<{ x: number, y: number }[]>([]);
    const [exclusionZones, setExclusionZones] = useState<{ x: number, y: number }[][]>(feed.config?.exclusion_zones ?? []);
    const [currentExclusionPoints, setCurrentExclusionPoints] = useState<{ x: number, y: number }[]>([]);
    const [isFullscreen, setIsFullscreen] = useState(false);

    const canvasRef = useRef<HTMLCanvasElement>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const toggleTimeoutRef = useRef<NodeJS.Timeout | null>(null);

    const isLive = isConnected && lastFrameRef.current !== null;
    const isLoading = !isConnected && !error;
    const isAdmin = userRole === UserRole.ADMIN;

    const component_name = feedName ?? `Feed ${feed_id}`;
    const component_node = `Source: ${source ?? 'N/A'}`;

    // Load existing ROI and filter settings if available
    useEffect(() => {
        if (feed && feed.config) {
            if (!roiMode && feed.config.roi) {
                setRoiPoints(feed.config.roi);
            }
            if (!roiMode && feed.config.exclusion_zones) {
                setExclusionZones(feed.config.exclusion_zones);
            }
            if (feed.config.static_object_filter_enabled !== undefined) {
                setStaticFilterEnabled(feed.config.static_object_filter_enabled);
            }
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

    useEffect(() => {
        const handleFullscreenChange = () => {
            setIsFullscreen(document.fullscreenElement === containerRef.current);
        };
        document.addEventListener('fullscreenchange', handleFullscreenChange);
        return () => document.removeEventListener('fullscreenchange', handleFullscreenChange);
    }, []);

    const handleToggleStaticFilter = (enabled: boolean) => {
        setStaticFilterEnabled(enabled);
        updateFeedConfig({ static_object_filter_enabled: enabled });
    };

    const handleCanvasClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
        if (!roiMode || !canvasRef.current) return;

        const rect = canvasRef.current.getBoundingClientRect();
        const x = (e.clientX - rect.left) / rect.width;
        const y = (e.clientY - rect.top) / rect.height;

        if (roiMode === 'roi') {
            setRoiPoints(prev => [...prev, { x, y }]);
        } else if (roiMode === 'exclusion') {
            setCurrentExclusionPoints(prev => [...prev, { x, y }]);
        }
    };

    const handleSaveROI = async () => {
        if (!feed_id) return;
        try {
            updateFeedConfig({ roi: roiPoints });
            setRoiMode(null);
            console.log("ROI Save command sent via WebSocket");
        } catch (error) {
            console.error("Error sending ROI update:", error);
        }
    };

    const handleSaveExclusionZone = async () => {
        if (!feed_id || currentExclusionPoints.length < 3) return;
        try {
            const newZones = [...exclusionZones, currentExclusionPoints];
            setExclusionZones(newZones);
            updateFeedConfig({ exclusion_zones: newZones });
            setCurrentExclusionPoints([]);
            setRoiMode(null);
        } catch (error) {
            console.error("Error saving exclusion zone:", error);
        }
    };

    const handleClearROI = () => {
        setRoiPoints([]);
    };

    const handleClearExclusionZones = () => {
        setExclusionZones([]);
        updateFeedConfig({ exclusion_zones: [] });
    };

    useEffect(() => {
        let animationFrameId: number;

        const render = () => {
            if (canvasRef.current) {
                const canvas = canvasRef.current;
                const ctx = canvas.getContext('2d', { alpha: false });
                if (ctx && lastFrameRef.current) {
                    const frame = lastFrameRef.current;
                    const frameWidth = frame.image?.width || 640;
                    const frameHeight = frame.image?.height || 480;

                    // Set internal resolution to match video source
                    if (canvas.width !== frameWidth || canvas.height !== frameHeight) {
                        canvas.width = frameWidth;
                        canvas.height = frameHeight;
                    }

                    // Draw Video and AI Overlays
                    drawFrame(ctx, frame, {
                        showBoundingBoxes: showBoundingBoxes,
                        showVehicleDetails: showVehicleDetails,
                        showTrajectories: showTrajectories
                    });

                    // Draw ROI - p.x and p.y are normalized [0, 1]
                    if (roiMode === 'roi' || (showOverlays && showROI && roiPoints.length > 0)) {
                        ctx.save();
                        ctx.strokeStyle = roiMode === 'roi' ? '#00ff00' : 'rgba(0, 255, 0, 0.3)';
                        ctx.lineWidth = 2; // Fixed width for better visibility
                        ctx.beginPath();
                        roiPoints.forEach((p, i) => {
                            const px = p.x * canvas.width;
                            const py = p.y * canvas.height;
                            if (i === 0) ctx.moveTo(px, py);
                            else ctx.lineTo(px, py);
                            if (roiMode === 'roi') {
                                ctx.fillStyle = '#00ff00';
                                ctx.fillRect(px - 4, py - 4, 8, 8); // Slightly larger handles
                            }
                        });
                        if (roiPoints.length > 2) ctx.closePath();
                        ctx.stroke();
                        if (roiPoints.length > 2) {
                            ctx.fillStyle = 'rgba(0, 255, 0, 0.05)';
                            ctx.fill();
                        }
                        ctx.restore();
                    }

                    // Draw Exclusion Zones
                    if (showOverlays && showExclusionZones) {
                        ctx.save();

                        // Draw established zones
                        exclusionZones.forEach(zone => {
                            ctx.strokeStyle = 'rgba(255, 0, 0, 0.3)';
                            ctx.lineWidth = 2;
                            ctx.beginPath();
                            zone.forEach((p, i) => {
                                const px = p.x * canvas.width;
                                const py = p.y * canvas.height;
                                if (i === 0) ctx.moveTo(px, py);
                                else ctx.lineTo(px, py);
                            });
                            if (zone.length > 2) {
                                ctx.closePath();
                                ctx.fillStyle = 'rgba(255, 0, 0, 0.1)';
                                ctx.fill();
                            }
                            ctx.stroke();
                        });

                        // Draw current being edited zone
                        if (roiMode === 'exclusion') {
                            ctx.strokeStyle = '#ff0000';
                            ctx.lineWidth = 3;
                            ctx.beginPath();
                            currentExclusionPoints.forEach((p, i) => {
                                const px = p.x * canvas.width;
                                const py = p.y * canvas.height;
                                if (i === 0) ctx.moveTo(px, py);
                                else ctx.lineTo(px, py);
                                ctx.fillStyle = '#ff0000';
                                ctx.fillRect(px - 5, py - 5, 10, 10);
                            });
                            ctx.stroke();
                        }
                        ctx.restore();
                    }
                } else if (ctx) {
                    // Clear canvas if not live
                    ctx.clearRect(0, 0, canvas.width, canvas.height);
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
    }, [isLive, drawFrame, showOverlays, showBoundingBoxes, showVehicleDetails, showTrajectories, showROI, showExclusionZones, feed_id, roiPoints, roiMode, exclusionZones, currentExclusionPoints, lastFrameRef]);

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
            // Only handle click outside for the custom inline panel (when in fullscreen)
            if (isFullscreen && showControlsPanel && containerRef.current && !containerRef.current.contains(event.target as Node)) {
                setShowControlsPanel(false);
            }
        };

        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, [showControlsPanel, containerRef, isFullscreen]);

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'f' || e.key === 'F') {
            toggleFullScreen();
        }
    };

    const renderOverlayControls = () => (
        <>
            {/* Congestion Gauge Overlay - Integrated here to show in both Dropdown and Fullscreen if needed, 
                but usually we only want it in the fullscreen panel or as a separate overlay. 
                Upstream put it inside the panel. */}
            {metrics && (
                <div className="flex flex-col items-end gap-1 mb-4 p-2 bg-black/40 rounded border border-lcd-text/20">
                    <div className="text-[10px] text-lcd-text/70 uppercase tracking-tighter font-lcd">Congestion</div>
                    <div className="w-24 h-1.5 bg-black/40 border border-lcd-text/20 overflow-hidden">
                        <div
                            className={cn(
                                "h-full transition-all duration-500",
                                (metrics.congestion_index || 0) > 70 ? "bg-red-500" :
                                    (metrics.congestion_index || 0) > 40 ? "bg-yellow-500" : "bg-primary"
                            )}
                            style={{ width: `${metrics.congestion_index || 0}%` }}
                        />
                    </div>
                    <div className="text-[8px] text-lcd-text/50 font-lcd uppercase">Index: {metrics.congestion_index || 0}%</div>
                </div>
            )}
            
            <StreamOverlayControls
                showOverlays={showOverlays}
                setShowOverlays={setShowOverlays}
                showBoundingBoxes={showBoundingBoxes}
                setShowBoundingBoxes={setShowBoundingBoxes}
                showVehicleDetails={showVehicleDetails}
                setShowVehicleDetails={setShowVehicleDetails}
                showROI={showROI}
                setShowROI={setShowROI}
                showTrajectories={showTrajectories}
                setShowTrajectories={setShowTrajectories}
                showExclusionZones={showExclusionZones}
                setShowExclusionZones={setShowExclusionZones}
                onClearExclusionZones={isAdmin ? handleClearExclusionZones : undefined}
                staticFilterEnabled={staticFilterEnabled}
                setStaticFilterEnabled={isAdmin ? handleToggleStaticFilter : undefined}
                controlId={feed_id}
            />
        </>
    );

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
                        className={cn("w-full h-full object-contain image-rendering-pixelated filter-contrast-125", roiMode && "cursor-crosshair")}
                        onClick={handleCanvasClick}
                    />
                ) : (
                    <div className="absolute inset-0 flex items-center justify-center opacity-30">
                        <Eye className="text-lcd-bg group-hover:text-lcd-text text-4xl" />
                    </div>
                )}

                {/* ROI/Exclusion Controls */}
                {isAdmin && roiMode && (
                    <div className="absolute top-2 left-1/2 -translate-x-1/2 bg-black/80 p-2 rounded flex gap-2 z-50">
                        <span className="text-white text-xs self-center mr-2">
                            {roiMode === 'roi' ? 'Set Inclusion ROI' : 'Add Exclusion Zone'}
                        </span>
                        {roiMode === 'roi' ? (
                            <>
                                <button onClick={handleClearROI} className="px-2 py-1 bg-red-600 text-white text-xs rounded hover:bg-red-700">Clear</button>
                                <button onClick={handleSaveROI} className="px-2 py-1 bg-green-600 text-white text-xs rounded hover:bg-green-700">Save ROI</button>
                            </>
                        ) : (
                            <>
                                <button onClick={() => setCurrentExclusionPoints([])} className="px-2 py-1 bg-red-600 text-white text-xs rounded hover:bg-red-700">Reset</button>
                                <button onClick={handleSaveExclusionZone} className="px-2 py-1 bg-green-600 text-white text-xs rounded hover:bg-green-700">Add Zone</button>
                            </>
                        )}
                        <button onClick={() => { setRoiMode(null); setCurrentExclusionPoints([]); }} className="px-2 py-1 bg-gray-600 text-white text-xs rounded hover:bg-gray-700">Cancel</button>
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
                            <>
                                <button
                                    onClick={(e) => { e.stopPropagation(); setRoiMode(roiMode === 'roi' ? null : 'roi'); }}
                                    title="Set Inclusion ROI"
                                    className={cn("p-1 text-lcd-bg group-hover:text-lcd-text bg-black/50 backdrop-blur-sm rounded-none hover:bg-black/70", roiMode === 'roi' && "bg-primary text-primary-foreground")}
                                >
                                    <Square className="h-4 w-4" style={{ transform: 'rotate(45deg)' }} />
                                </button>
                                <button
                                    onClick={(e) => { e.stopPropagation(); setRoiMode(roiMode === 'exclusion' ? null : 'exclusion'); }}
                                    title="Add Exclusion Zone"
                                    className={cn("p-1 text-lcd-bg group-hover:text-lcd-text bg-black/50 backdrop-blur-sm rounded-none hover:bg-black/70", roiMode === 'exclusion' && "bg-red-600 text-white")}
                                >
                                    <AlertTriangle className="h-4 w-4" />
                                </button>
                            </>
                        )}
                    </div>
                )}

                {!isToggling && !isLoading && !error && !minimalControls && (
                    <div className="absolute top-12 right-1.5 flex flex-col gap-2 z-20 opacity-0 group-hover:opacity-100 transition-opacity duration-300">
                         {isFullscreen ? (
                            <button
                                className="text-lcd-bg group-hover:text-lcd-text p-1 rounded-none bg-black/50 backdrop-blur-sm hover:bg-black/70"
                                onClick={(e) => { e.stopPropagation(); setShowControlsPanel(!showControlsPanel); }}
                                title="Overlay Controls"
                            >
                                <Settings className="h-4 w-4" />
                            </button>
                        ) : (
                            <DropdownMenu open={showControlsPanel} onOpenChange={setShowControlsPanel}>
                                <DropdownMenuTrigger asChild>
                                    <button
                                        className="text-lcd-bg group-hover:text-lcd-text p-1 rounded-none bg-black/50 backdrop-blur-sm hover:bg-black/70"
                                        title="Overlay Controls"
                                        onClick={(e) => e.stopPropagation()} // Prevent trigger from bubbling
                                    >
                                        <Settings className="h-4 w-4" />
                                    </button>
                                </DropdownMenuTrigger>
                                <DropdownMenuContent 
                                    className="p-0 border-0 bg-transparent shadow-none" 
                                    align="end"
                                    side="right"
                                    sideOffset={5}
                                >
                                    {renderOverlayControls()}
                                </DropdownMenuContent>
                            </DropdownMenu>
                        )}
                        <button
                            className="text-lcd-bg group-hover:text-lcd-text p-1 rounded-none bg-black/50 backdrop-blur-sm hover:bg-black/70"
                            onClick={(e) => { e.stopPropagation(); toggleFullScreen(); }}
                            title="Fullscreen"
                        >
                            <Maximize className="h-4 w-4" />
                        </button>
                    </div>
                )}
                
                {isFullscreen && showControlsPanel && (
                    <div className="absolute top-12 right-8 z-30">
                        {renderOverlayControls()}
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