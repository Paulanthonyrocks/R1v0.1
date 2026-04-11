import React, { useState, useEffect, useRef, forwardRef, memo } from 'react';
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Eye, AlertTriangle, Loader2, RotateCw, Settings, Play, Square, Maximize } from 'lucide-react';
import { cn } from "@/lib/utils";
import type { SurveillanceFeedProps } from '@/lib/types';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import useVideoSocket from '@/lib/useVideoSocket';
import { useAuth } from '@/lib/auth/AuthProvider';
import { useWebSocket } from '@/lib/websocket/WebSocketProvider';
import { useVehicleSelection } from '@/lib/context/VehicleSelectionContext';
import { UserRole } from '@/lib/auth/roles';
import StreamOverlayControls from './StreamOverlayControls';
import MetricsPanel from './MetricsPanel';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import IdentityGallery from '../feature/IdentityGallery';

const SurveillanceFeed = memo(forwardRef<HTMLDivElement, SurveillanceFeedProps>(({ feed, minimalControls = false }, ref) => {
    const { feed_id, name: feedName, source, status } = feed;
    const { startFeed, stopFeed, restartFeed } = useRealtimeUpdates();
    const { token, userRole } = useAuth();
    const wsClient = useWebSocket();
    const { selectedGlobalId, setSelectedGlobalId } = useVehicleSelection();

    // Only subscribe if the feed is in an active state
    const shouldSubscribe = status === 'running' || status === 'starting';
    const { lastFrameRef, metrics, isConnected, error, drawFrame, frameRate: fps, vehicles, updateFeedConfig } = useVideoSocket(
        shouldSubscribe ? feed_id : "",
        token
    );

    const [isToggling, setIsToggling] = useState<boolean>(false);
    const [showOverlays, setShowOverlays] = useState<boolean>(true);
    const [showBoundingBoxes, setShowBoundingBoxes] = useState<boolean>(feed.config?.show_bounding_boxes ?? true);
    const [showVehicleDetails, setShowVehicleDetails] = useState<boolean>(feed.config?.show_vehicle_details ?? true);
    const [showTrajectories, setShowTrajectories] = useState<boolean>(feed.config?.show_trajectories ?? true);
    const [showROI, setShowROI] = useState<boolean>(false);
    const [showExclusionZones, setShowExclusionZones] = useState<boolean>(true);
    const [showLaneOverlays, setShowLaneOverlays] = useState<boolean>(false);
    const [showAllDetections, setShowAllDetections] = useState<boolean>(false);
    const [selectedVehicleIds, setSelectedVehicleIds] = useState<Set<string>>(new Set());
    const [showControlsPanel, setShowControlsPanel] = useState<boolean>(false);
    const [staticFilterEnabled, setStaticFilterEnabled] = useState<boolean>(feed.config?.static_object_filter_enabled ?? false);

    // Sync selections to Backend
    useEffect(() => {
        if (feed_id && wsClient) {
            console.debug(`[SurveillanceFeed] Syncing selections for ${feed_id}:`, Array.from(selectedVehicleIds));
            wsClient.sendCommand("SET_SELECTED_IDS", feed_id, Array.from(selectedVehicleIds));
        }
    }, [selectedVehicleIds, feed_id, wsClient]);

    const [roiMode, setRoiMode] = useState<'roi' | 'exclusion' | null>(null);
    const [roiPoints, setRoiPoints] = useState<{ x: number, y: number }[]>([]);
    const [exclusionZones, setExclusionZones] = useState<{ x: number, y: number }[][]>(feed.config?.exclusion_zones ?? []);
    const [currentExclusionPoints, setCurrentExclusionPoints] = useState<{ x: number, y: number }[]>([]);
    const [isFullscreen, setIsFullscreen] = useState(false);

    // Tracks global_vehicle_id → last known vehicle_id for sticky cleanup
    const stickyMapRef = useRef<Map<string, string>>(new Map());

    // Sticky Selection Logic:
    // When a tracked vehicle's tracker ID changes (e.g. due to frame-skip recovery
    // or ReID-only re-acquisition), but its global_vehicle_id remains the same,
    // automatically update the selection to follow the new tracker ID.
    // Also promotes vehicle_id–based selections to global_vehicle_id when one appears.
    useEffect(() => {
        if (!vehicles || vehicles.length === 0) return;

        setSelectedVehicleIds(prev => {
            let next: Set<string> | null = null; // lazy copy – avoid re-render if nothing changed

            for (const v of vehicles) {
                const gid = v.global_vehicle_id;
                if (!gid) continue;

                // Case 1: global_vehicle_id is already selected → clean up any stale tracker ID
                if (prev.has(gid)) {
                    const lastKnown = stickyMapRef.current.get(gid);
                    if (lastKnown && lastKnown !== v.vehicle_id && prev.has(lastKnown)) {
                        if (!next) next = new Set(prev);
                        next.delete(lastKnown);
                    }
                    stickyMapRef.current.set(gid, v.vehicle_id);
                    continue;
                }

                // Case 2: vehicle was selected by its vehicle_id before a global_vehicle_id
                // was assigned. Promote the selection to the global_vehicle_id for stickiness.
                if (prev.has(v.vehicle_id)) {
                    if (!next) next = new Set(prev);
                    next.delete(v.vehicle_id);
                    next.add(gid);
                    stickyMapRef.current.set(gid, v.vehicle_id);
                }
            }

            return next ?? prev;
        });
    }, [vehicles]);

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
            if (feed.config.show_bounding_boxes !== undefined) {
                setShowBoundingBoxes(feed.config.show_bounding_boxes);
            }
            if (feed.config.show_vehicle_details !== undefined) {
                setShowVehicleDetails(feed.config.show_vehicle_details);
            }
            if (feed.config.show_trajectories !== undefined) {
                setShowTrajectories(feed.config.show_trajectories);
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

    const handleToggleBoundingBoxes = (enabled: boolean) => {
        setShowBoundingBoxes(enabled);
        updateFeedConfig({ show_bounding_boxes: enabled });
    };

    const handleToggleVehicleDetails = (enabled: boolean) => {
        setShowVehicleDetails(enabled);
        updateFeedConfig({ show_vehicle_details: enabled });
    };

    const handleToggleTrajectories = (enabled: boolean) => {
        setShowTrajectories(enabled);
        updateFeedConfig({ show_trajectories: enabled });
    };

    const handleCanvasClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
        if (!canvasRef.current) return;

        const canvas = canvasRef.current;
        const rect = canvas.getBoundingClientRect();

        // Get intrinsic dimensions of the video content
        const contentWidth = canvas.width;
        const contentHeight = canvas.height;

        if (contentWidth === 0 || contentHeight === 0) return;

        // Get displayed dimensions of the canvas element
        const containerWidth = rect.width;
        const containerHeight = rect.height;

        const contentRatio = contentWidth / contentHeight;
        const containerRatio = containerWidth / containerHeight;

        let actualWidth, actualHeight, offsetX, offsetY;

        // Calculate the actual dimensions and offsets of the "contained" video
        if (containerRatio > contentRatio) {
            // Pillarbox (black bars on left/right)
            actualHeight = containerHeight;
            actualWidth = containerHeight * contentRatio;
            offsetX = (containerWidth - actualWidth) / 2;
            offsetY = 0;
        } else {
            // Letterbox (black bars on top/bottom)
            actualWidth = containerWidth;
            actualHeight = containerWidth / contentRatio;
            offsetX = 0;
            offsetY = (containerHeight - actualHeight) / 2;
        }

        // Calculate normalized coordinates relative to the actual video content
        const xNormalized = (e.clientX - rect.left - offsetX) / actualWidth;
        const yNormalized = (e.clientY - rect.top - offsetY) / actualHeight;

        // Clamp to [0, 1] to ensure we stay within video bounds
        const xClamped = Math.max(0, Math.min(1, xNormalized));
        const yClamped = Math.max(0, Math.min(1, yNormalized));

        if (roiMode) {
            const point = { x: xClamped, y: yClamped };
            if (roiMode === 'roi') {
                const newPoints = [...roiPoints, point];
                setRoiPoints(newPoints);
                if (newPoints.length === 4) setRoiMode(null);
            } else if (roiMode === 'exclusion') {
                setCurrentExclusionPoints([...currentExclusionPoints, point]);
            }
            return;
        }

        // Vehicle Selection Logic (only if not in ROI mode)
        // Find vehicle under cursor using normalized coordinates
        const clickedVehicle = vehicles?.find(v => {
            if (!v.bbox || !Array.isArray(v.bbox)) return false;
            const [x1, y1, x2, y2] = v.bbox;
            return xClamped >= x1 && xClamped <= x2 && yClamped >= y1 && yClamped <= y2;
        });

        if (clickedVehicle) {
            const vid = clickedVehicle.vehicle_id;
            const gid = clickedVehicle.global_vehicle_id;
            const idToToggle = gid || vid;
            const isDeselecting = selectedVehicleIds.has(idToToggle);

            setSelectedVehicleIds(prev => {
                const newSet = new Set(prev);
                if (newSet.has(idToToggle)) {
                    newSet.delete(idToToggle);
                } else {
                    newSet.add(idToToggle);
                }
                return newSet;
            });

            // Update gallery panel & sticky map
            if (gid) {
                if (isDeselecting) {
                    // Clear gallery only if this was the displayed global ID
                    setSelectedGlobalId(null);
                } else {
                    setSelectedGlobalId(gid);
                    stickyMapRef.current.set(gid, vid);
                }
            } else {
                setSelectedGlobalId(null);
            }
        } else {
            // Click on empty area — no-op (preserve multi-selection)
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
                        showTrajectories: showTrajectories,
                        showLaneOverlays: showLaneOverlays,
                        selectedVehicleIds: selectedVehicleIds,
                        showAllDetections: showAllDetections
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
    }, [isConnected, drawFrame, showOverlays, showBoundingBoxes, showVehicleDetails, showTrajectories, showROI, showExclusionZones, feed_id, roiPoints, roiMode, exclusionZones, currentExclusionPoints, lastFrameRef, showAllDetections, selectedVehicleIds]);

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
            <MetricsPanel
                metrics={metrics}
                isLive={isLive}
                className="mb-4 border-primary/20 bg-black/60 shadow-none"
            />

            <StreamOverlayControls
                showOverlays={showOverlays}
                setShowOverlays={setShowOverlays}
                showBoundingBoxes={showBoundingBoxes}
                setShowBoundingBoxes={isAdmin ? handleToggleBoundingBoxes : setShowBoundingBoxes}
                showAllDetections={showAllDetections}
                setShowAllDetections={setShowAllDetections}
                showVehicleDetails={showVehicleDetails}
                setShowVehicleDetails={isAdmin ? handleToggleVehicleDetails : setShowVehicleDetails}
                showROI={showROI}
                setShowROI={setShowROI}
                showTrajectories={showTrajectories}
                setShowTrajectories={isAdmin ? handleToggleTrajectories : setShowTrajectories}
                showLaneOverlays={showLaneOverlays}
                setShowLaneOverlays={setShowLaneOverlays}
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
            <div ref={containerRef} className="bg-black flex items-center justify-center relative group overflow-hidden">
                {isLive ? (
                    <canvas
                        ref={canvasRef}
                        className={cn("w-full h-full image-rendering-pixelated filter-contrast-125", roiMode && "cursor-crosshair")}
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
                    <Badge variant="outline" className="absolute top-1.5 left-1.5 text-[10px] h-4 px-1.5 bg-black/40 text-primary-foreground/70 backdrop-blur-[2px] rounded-none border-0 font-mono">
                        {fps.toFixed(0)} FPS
                    </Badge>
                )}

                <Badge
                    variant={isLive ? "default" : "outline"}
                    className={cn(
                        "absolute bottom-1.5 right-1.5 text-[10px] h-4 px-1.5 rounded-none font-mono border-0",
                        isLive
                            ? "bg-primary/80 text-primary-foreground"
                            : "bg-zinc-800 text-zinc-400",
                    )}
                >
                    {isLive ? "LIVE" : status?.toUpperCase() ?? "UNKNOWN"}
                </Badge>

                {isLive && metrics?.calibration && (
                    <Badge
                        variant="outline"
                        className={cn(
                            "absolute bottom-1.5 right-12 text-[10px] h-4 px-1.5 rounded-none font-mono border-0 bg-black/40",
                            Object.values(metrics.calibration).every((c: any) => c.calibrated)
                                ? "text-green-400"
                                : "text-yellow-400"
                        )}
                    >
                        {Object.values(metrics.calibration).every((c: any) => c.calibrated)
                            ? "OPTIMIZED"
                            : "CALIBRATING..."}
                    </Badge>
                )}

                {!minimalControls && (
                    <MetricsPanel
                        metrics={metrics}
                        isLive={isLive}
                        className="absolute top-1.5 right-1.5 z-20 w-48 opacity-0 group-hover:opacity-100 transition-opacity duration-200 pointer-events-none group-hover:pointer-events-auto scale-95 origin-top-right"
                    />
                )}

                {!isToggling && !isLoading && !minimalControls && (
                    <div className="absolute bottom-1.5 left-1.5 flex gap-1 z-20 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
                        <div className="flex bg-black/60 backdrop-blur-sm p-0.5 rounded-sm">
                            <button
                                onClick={(e) => { e.stopPropagation(); handleRestartFeed(); }}
                                title="Restart Feed"
                                className="p-1 hover:bg-white/10 rounded-sm text-zinc-300 hover:text-white transition-colors"
                            >
                                <RotateCw className="h-3 w-3" />
                            </button>
                            <div className="w-px bg-white/10 mx-0.5 my-1" />
                            <button
                                onClick={(e) => { e.stopPropagation(); handleStartFeed(); }}
                                disabled={isToggling || status === 'running' || status === 'starting'}
                                title="Start Feed"
                                className="p-1 hover:bg-white/10 rounded-sm text-zinc-300 hover:text-white transition-colors disabled:opacity-30"
                            >
                                <Play className="h-3 w-3" />
                            </button>
                            <button
                                onClick={(e) => { e.stopPropagation(); handleStopFeed(); }}
                                disabled={isToggling || status === 'stopped' || status === 'error'}
                                title="Stop Feed"
                                className="p-1 hover:bg-white/10 rounded-sm text-zinc-300 hover:text-white transition-colors disabled:opacity-30"
                            >
                                <Square className="h-3 w-3" />
                            </button>
                        </div>

                        {isAdmin && (
                            <div className="flex bg-black/60 backdrop-blur-sm p-0.5 rounded-sm ml-1">
                                <button
                                    onClick={(e) => { e.stopPropagation(); setRoiMode(roiMode === 'roi' ? null : 'roi'); }}
                                    title="Set ROI"
                                    className={cn("p-1 hover:bg-white/10 rounded-sm text-zinc-300 hover:text-white transition-colors", roiMode === 'roi' && "text-green-400")}
                                >
                                    <Square className="h-3 w-3 rotate-45" />
                                </button>
                                <button
                                    onClick={(e) => { e.stopPropagation(); setRoiMode(roiMode === 'exclusion' ? null : 'exclusion'); }}
                                    title="Add Exclusion Zone"
                                    className={cn("p-1 hover:bg-white/10 rounded-sm text-zinc-300 hover:text-white transition-colors", roiMode === 'exclusion' && "text-red-400")}
                                >
                                    <AlertTriangle className="h-3 w-3" />
                                </button>
                            </div>
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

                {/* Identity Gallery Side Panel */}
                {selectedGlobalId && (
                    <div className="absolute top-4 right-4 bottom-4 w-72 z-50 pointer-events-auto">
                        <IdentityGallery
                            globalId={selectedGlobalId}
                            onClose={() => setSelectedGlobalId(null)}
                        />
                    </div>
                )}
            </div>
            <CardContent className="p-2">
                <div className="flex items-center justify-between gap-2">
                    <h4 className="font-bold text-xs truncate text-lcd-bg group-hover:text-lcd-text transition-colors tracking-normal font-lcd matrix-glow">
                        {component_name}
                    </h4>
                    <p className="text-xs text-lcd-bg/60 group-hover:text-lcd-text/80 transition-colors truncate tracking-normal font-lcd matrix-glow">
                        {component_node}
                    </p>
                </div>
            </CardContent>
        </Card>
    );
}), (prevProps, nextProps) => {
    // Custom equality check to prevent re-renders when only metrics in the feed object change
    // (since we use useVideoSocket for live metrics)
    return prevProps.feed.feed_id === nextProps.feed.feed_id && 
           prevProps.feed.status === nextProps.feed.status &&
           prevProps.feed.name === nextProps.feed.name &&
           prevProps.feed.source === nextProps.feed.source &&
           prevProps.minimalControls === nextProps.minimalControls &&
           JSON.stringify(prevProps.feed.config) === JSON.stringify(nextProps.feed.config);
});

SurveillanceFeed.displayName = 'SurveillanceFeed';
export default SurveillanceFeed;
