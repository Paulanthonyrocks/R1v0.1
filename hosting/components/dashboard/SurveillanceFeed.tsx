import React, { useState, useEffect, useRef, forwardRef, memo } from 'react';
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Eye, AlertTriangle, Loader2, RotateCw, Settings, Play, Square, Maximize } from 'lucide-react';
import { cn } from "@/lib/utils";
import { formatFeedName, formatFeedSource } from "@/lib/formatters";
import type { SurveillanceFeedProps } from '@/lib/types';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import useVideoSocket from '@/lib/useVideoSocket';
import { useAuth } from '@/lib/auth/AuthProvider';
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

const SurveillanceFeed = memo(forwardRef<HTMLDivElement, SurveillanceFeedProps>(({ feed_id, minimalControls = false }, ref) => {
  const instanceId = useRef(Math.random().toString(36).substring(2, 9));
  const { feeds, startFeed, stopFeed, restartFeed } = useRealtimeUpdates();
  const feed = React.useMemo(() => feeds.find(f => f.feed_id === feed_id), [feeds, feed_id]);

  if (!feed) {
    return (
      <div className="bg-black aspect-video flex items-center justify-center">
        <Loader2 className="animate-spin h-10 w-10 text-lcd-text/20" />
      </div>
    );
  }

  const { name: feedName, source, status } = feed;
  const { token, userRole } = useAuth();
  const { selectedGlobalId, setSelectedGlobalId } = useVehicleSelection();

  // Only subscribe if the feed is in an active state
  const shouldSubscribe = status === 'running' || status === 'starting';
  const { lastFrameRef, metrics, isConnected, error, drawFrame, frameRate: fps, vehicles, updateFeedConfig, notifyFrameRendered } = useVideoSocket(
    shouldSubscribe ? feed_id : "",
    minimalControls  // Skip vehicle data in minimal mode to reduce memory/GC pressure
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

    // Refs for the render loop to avoid effect restarts
    const renderOptionsRef = useRef({
        showOverlays,
        showBoundingBoxes,
        showVehicleDetails,
        showTrajectories,
        showROI,
        showExclusionZones,
        showLaneOverlays,
        showAllDetections,
        selectedVehicleIds,
    });

    const roiStateRef = useRef({
        roiPoints: [] as { x: number, y: number }[],
        roiMode: null as 'roi' | 'exclusion' | null,
        exclusionZones: [] as { x: number, y: number }[][],
        currentExclusionPoints: [] as { x: number, y: number }[],
    });

    const [roiMode, setRoiMode] = useState<'roi' | 'exclusion' | null>(null);
    const [roiPoints, setRoiPoints] = useState<{ x: number, y: number }[]>([]);
    const [exclusionZones, setExclusionZones] = useState<{ x: number, y: number }[][]>(feed.config?.exclusion_zones ?? []);
    const [currentExclusionPoints, setCurrentExclusionPoints] = useState<{ x: number, y: number }[]>([]);
    const [isFullscreen, setIsFullscreen] = useState(false);

    // Keep refs in sync with state
    useEffect(() => {
        renderOptionsRef.current = {
            showOverlays,
            showBoundingBoxes,
            showVehicleDetails,
            showTrajectories,
            showROI,
            showExclusionZones,
            showLaneOverlays,
            showAllDetections,
            selectedVehicleIds,
        };
    }, [showOverlays, showBoundingBoxes, showVehicleDetails, showTrajectories, showROI, showExclusionZones, showLaneOverlays, showAllDetections, selectedVehicleIds]);

    useEffect(() => {
        roiStateRef.current = {
            roiPoints,
            roiMode,
            exclusionZones,
            currentExclusionPoints,
        };
    }, [roiPoints, roiMode, exclusionZones, currentExclusionPoints]);

    // Tracks global_vehicle_id → last known vehicle_id for sticky cleanup
    const stickyMapRef = useRef<Map<string, string>>(new Map());

    // Client-side trajectory history per vehicle (normalized centroids). The
    // wire payload carries no track history, so we accumulate it here and
    // prune entries when a track disappears.
    const trajectoryMapRef = useRef<Map<string, { x: number, y: number }[]>>(new Map());

    useEffect(() => {
        const currentVehicles = vehicles;
        if (!currentVehicles || currentVehicles.length === 0) return;

        setSelectedVehicleIds(prev => {
            let next: Set<string> | null = null;

            for (const v of currentVehicles) {
                const gid = v.global_vehicle_id;
                if (!gid) continue;

                if (prev.has(gid)) {
                    const lastKnown = stickyMapRef.current.get(gid);
                    if (lastKnown && lastKnown !== v.vehicle_id && prev.has(lastKnown)) {
                        if (!next) next = new Set(prev);
                        next.delete(lastKnown);
                    }
                    stickyMapRef.current.set(gid, v.vehicle_id);
                    continue;
                }

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

    const component_name = formatFeedName(feedName, feed_id);

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

        const contentWidth = canvas.width;
        const contentHeight = canvas.height;

        if (contentWidth === 0 || contentHeight === 0) return;

        const containerWidth = rect.width;
        const containerHeight = rect.height;

        const contentRatio = contentWidth / contentHeight;
        const containerRatio = containerWidth / containerHeight;

        let actualWidth, actualHeight, offsetX, offsetY;

        if (containerRatio > contentRatio) {
            actualHeight = containerHeight;
            actualWidth = containerHeight * contentRatio;
            offsetX = (containerWidth - actualWidth) / 2;
            offsetY = 0;
        } else {
            actualWidth = containerWidth;
            actualHeight = containerWidth / contentRatio;
            offsetX = 0;
            offsetY = (containerHeight - actualHeight) / 2;
        }

        const xNormalized = (e.clientX - rect.left - offsetX) / actualWidth;
        const yNormalized = (e.clientY - rect.top - offsetY) / actualHeight;

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

            if (gid) {
                if (isDeselecting) {
                    setSelectedGlobalId(null);
                } else {
                    setSelectedGlobalId(gid);
                    stickyMapRef.current.set(gid, vid);
                }
            } else {
                setSelectedGlobalId(null);
            }
        }
    };

    const handleSaveROI = async () => {
        if (!feed_id) return;
        try {
            updateFeedConfig({ roi: roiPoints });
            setRoiMode(null);
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
        let lastDrawnIndex = -1;

        const render = () => {
            if (canvasRef.current) {
                const canvas = canvasRef.current;
                const ctx = canvas.getContext('2d', { alpha: false });
                
                if (ctx && lastFrameRef.current) {
                    const frame = lastFrameRef.current;
                    
                    if (frame.index === lastDrawnIndex) {
                        animationFrameId = requestAnimationFrame(render);
                        return;
                    }

                    const frameWidth = frame.image?.width || 640;
                    const frameHeight = frame.image?.height || 480;

                    if (frame.index % 100 === 0) {
                        console.debug(`[SurveillanceFeed Render] Component for ${feed_id} is drawing frame ${frame.index} from feed ${feed_id}`);
                    }

                    if (canvas.width !== frameWidth || canvas.height !== frameHeight) {
                        canvas.width = frameWidth;
                        canvas.height = frameHeight;
                    }

                    const opts = renderOptionsRef.current;
                    const roi = roiStateRef.current;

                    drawFrame(ctx, frame, {
                        showBoundingBoxes: opts.showBoundingBoxes,
                        showVehicleDetails: opts.showVehicleDetails,
                        showTrajectories: opts.showTrajectories,
                        showLaneOverlays: opts.showLaneOverlays,
                        selectedVehicleIds: opts.selectedVehicleIds,
                        showAllDetections: opts.showAllDetections,
                        minimal: minimalControls  // Dashboard grid: skip per-vehicle canvas overlays
                    });

                    // Signal the hook so it can release the previous frame's
                    // ImageBitmap safely now that the canvas has been painted.
                    notifyFrameRendered(frame.index);
                    lastDrawnIndex = frame.index;

                    // Trajectory overlays: accumulate bbox-center history per
                    // vehicle and draw polylines when the toggle is on. Gated
                    // off in minimal (grid) mode where vehicle data is absent.
                    if (opts.showTrajectories && !minimalControls && frame.vehicles && frame.vehicles.length > 0) {
                        const trajMap = trajectoryMapRef.current;
                        const seen = new Set<string>();
                        for (const v of frame.vehicles) {
                            if (!v.bbox || !Array.isArray(v.bbox) || v.bbox.length !== 4) continue;
                            const id = v.global_vehicle_id || v.vehicle_id;
                            if (!id) continue;
                            seen.add(id);
                            const cx = (v.bbox[0] + v.bbox[2]) / 2;
                            const cy = (v.bbox[1] + v.bbox[3]) / 2;
                            let pts = trajMap.get(id);
                            if (!pts) {
                                pts = [];
                                trajMap.set(id, pts);
                            }
                            const last = pts[pts.length - 1];
                            if (!last || Math.abs(last.x - cx) > 0.01 || Math.abs(last.y - cy) > 0.01) {
                                pts.push({ x: cx, y: cy });
                                if (pts.length > 40) pts.shift();
                            }
                        }
                        for (const id of Array.from(trajMap.keys())) {
                            if (!seen.has(id)) trajMap.delete(id);
                        }
                        ctx.save();
                        ctx.strokeStyle = 'rgba(0, 255, 200, 0.55)';
                        ctx.lineWidth = 1.5;
                        for (const pts of trajMap.values()) {
                            if (pts.length < 2) continue;
                            ctx.beginPath();
                            pts.forEach((p, i) => {
                                const px = p.x * canvas.width;
                                const py = p.y * canvas.height;
                                if (i === 0) ctx.moveTo(px, py);
                                else ctx.lineTo(px, py);
                            });
                            ctx.stroke();
                        }
                        ctx.restore();
                    }

                    if (roi.roiMode === 'roi' || (opts.showOverlays && opts.showROI && roi.roiPoints.length > 0)) {
                        ctx.save();
                        ctx.strokeStyle = roi.roiMode === 'roi' ? '#00ff00' : 'rgba(0, 255, 0, 0.3)';
                        ctx.lineWidth = 2;
                        ctx.beginPath();
                        roi.roiPoints.forEach((p, i) => {
                            const px = p.x * canvas.width;
                            const py = p.y * canvas.height;
                            if (i === 0) ctx.moveTo(px, py);
                            else ctx.lineTo(px, py);
                            if (roi.roiMode === 'roi') {
                                ctx.fillStyle = '#00ff00';
                                ctx.fillRect(px - 4, py - 4, 8, 8);
                            }
                        });
                        if (roi.roiPoints.length > 2) ctx.closePath();
                        ctx.stroke();
                        if (roi.roiPoints.length > 2) {
                            ctx.fillStyle = 'rgba(0, 255, 0, 0.05)';
                            ctx.fill();
                        }
                        ctx.restore();
                    }

                    if (opts.showOverlays && opts.showExclusionZones) {
                        ctx.save();

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

                        if (roi.roiMode === 'exclusion') {
                            ctx.strokeStyle = '#ff0000';
                            ctx.lineWidth = 3;
                            ctx.beginPath();
                            roi.currentExclusionPoints.forEach((p, i) => {
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

                    // (lastDrawnIndex is now set immediately above notifyFrameRendered)
                } else if (ctx) {
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
    }, [isConnected, drawFrame, feed_id, notifyFrameRendered]);

    const handleStartFeed = () => {
        if (isToggling) return;
        if (!feed_id) return;
        setIsToggling(true);
        startFeed(feed_id);
        if (toggleTimeoutRef.current) clearTimeout(toggleTimeoutRef.current);
        toggleTimeoutRef.current = setTimeout(() => setIsToggling(false), 5000);
    };

    const handleStopFeed = () => {
        if (isToggling) return;
        if (!feed_id) return;
        setIsToggling(true);
        stopFeed(feed_id);
        if (toggleTimeoutRef.current) clearTimeout(toggleTimeoutRef.current);
        toggleTimeoutRef.current = setTimeout(() => setIsToggling(false), 5000);
    };

    const handleRestartFeed = () => {
        if (!feed_id) return;
        setIsToggling(true);
        restartFeed(feed_id);
        if (toggleTimeoutRef.current) clearTimeout(toggleTimeoutRef.current);
        toggleTimeoutRef.current = setTimeout(() => setIsToggling(false), 5000);
    };

    const toggleFullScreen = () => {
        if (containerRef.current) {
            if (!document.fullscreenElement) {
                containerRef.current.requestFullscreen().catch(err => {
                    console.error(`Error attempting to enable full-screen mode: ${err.message}`);
                });
            } else {
                document.exitFullscreen();
            }
        }
    };

    useEffect(() => {
        const handleClickOutside = (event: MouseEvent) => {
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
                hidePerVehicleToggles={minimalControls}
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
                <canvas
                    ref={canvasRef}
                    className={cn("w-full h-full image-rendering-pixelated filter-contrast-125", roiMode && "cursor-crosshair")}
                    onClick={handleCanvasClick}
                />
                {!lastFrameRef.current && (
                    <div className="absolute inset-0 flex items-center justify-center opacity-30 pointer-events-none">
                        <Eye className="text-lcd-bg group-hover:text-lcd-text text-4xl" />
                    </div>
                )}

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

                <MetricsPanel
                    metrics={metrics}
                    isLive={isLive}
                    className="absolute top-1.5 right-1.5 z-20 w-48 opacity-0 group-hover:opacity-100 transition-opacity duration-200 pointer-events-none group-hover:pointer-events-auto scale-95 origin-top-right"
                />

                {!isToggling && (!isLoading || status === 'stopped') && (
                    <div className="absolute bottom-1.5 left-1.5 flex gap-1 z-20 opacity-0 group-hover:opacity-100 transition-opacity duration-200 pointer-events-none group-hover:pointer-events-auto">
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

                {!isToggling && !isLoading && !error && (
                    <div className="absolute top-12 right-1.5 flex flex-col gap-2 z-20 opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none group-hover:pointer-events-auto">
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
                                        onClick={(e) => e.stopPropagation()}
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

                {!minimalControls && selectedGlobalId && (
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
                </div>
            </CardContent>
        </Card>
    );
}), (prevProps, nextProps) => {
    return prevProps.feed_id === nextProps.feed_id && 
           prevProps.minimalControls === nextProps.minimalControls;
});

SurveillanceFeed.displayName = 'SurveillanceFeed';
export default SurveillanceFeed;
