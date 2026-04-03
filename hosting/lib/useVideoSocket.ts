import { useState, useEffect, useCallback, useRef } from 'react';
import { WebSocketMessageType } from './websocket/WebSocketClient';
import { useWebSocket } from './websocket/WebSocketProvider';
import { SurveillanceFeedMessage, VideoFrameMessage } from './types';

interface VehicleFrontendData {
    vehicle_id: string;
    global_vehicle_id?: string;
    bbox: [number, number, number, number];
    speed: number;
    license_plate: string;
    class_id: number;
    class_name: string;
    behavior: string;
    confidence: number;
    is_occluded: boolean;
    lane: number;
    status: 'active' | 'tentative' | 'predicting' | 'unknown';
    vx?: number;
    vy?: number;
    is_wrong_way?: boolean;
    is_stopped?: boolean;
}

const useVideoSocket = (streamId: string, token: string | null) => {
    const client = useWebSocket();
    const frameRef = useRef<{
        image: ImageBitmap | HTMLImageElement | null,
        index: number,
        vehicles: VehicleFrontendData[] | null,
        metrics: SurveillanceFeedMessage | null
    } | null>(null);

    const [metrics, setMetrics] = useState<SurveillanceFeedMessage | null>(null);
    const [vehicles, setVehicles] = useState<VehicleFrontendData[] | null>(null);
    const [isConnected, setIsConnected] = useState(client.isConnected());
    const [error, setError] = useState<string | null>(null);
    const [frameRate, setFrameRate] = useState<number>(0);
    const lastFrameTimeRef = useRef<number>(0);
    const smoothedFrameTimeRef = useRef<number>(0);
    const lastFpsUpdateRef = useRef<number>(0);
    const lastStateUpdateRef = useRef<number>(0);
    const lastVehiclesUpdateTimeRef = useRef<number>(performance.now());
    const lastSuccessfulFrameTimeRef = useRef<number>(performance.now()); // Track last successful frame process
    const STATE_UPDATE_INTERVAL = 200; // Update UI state at most every 200ms (5fps)
    const ANNOTATION_PERSISTENCE_MS = 500; // Retain annotations for 500ms during lag
    const FRAME_STALENESS_THRESHOLD = 5000; // 5 seconds of no frames = stale
    const FPS_EMA_ALPHA = 0.1;

    const frameCountRef = useRef<number>(0);
    const lastDrawnIndexRef = useRef<number>(-1);
    const lastProcessedIndexRef = useRef<number>(-1);
    
    // Registry to store full vehicle states for merging delta updates
    const vehicleRegistryRef = useRef<Map<string, VehicleFrontendData>>(new Map());
    const lastSeenVehicleTimeRef = useRef<Map<string, number>>(new Map());

    const subscribeToFeed = useCallback(() => {
        if (client.isConnected() && streamId) {
            console.log(`[useVideoSocket] Subscribing to feed ${streamId}. Client: ${client.getInstanceId()}`);
            client.send({
                type: WebSocketMessageType.SUBSCRIBE_TO_FEED,
                data: { feed_id: streamId },
            });
        } else {
            console.log(`[useVideoSocket] subscribeToFeed skipped. isConnected=${client.isConnected()}, streamId=${streamId}`);
        }
    }, [client, streamId]);

    const handleFrame = useCallback(async (data: VideoFrameMessage) => {
        // Strict filtering: If feed_id is missing or mismatch, drop it.
        if (!data.feed_id || data.feed_id !== streamId) {
            return;
        }

        // Drop late frames (out of order), but allow for loops
        if (data.frame_index !== undefined && data.frame_index < lastProcessedIndexRef.current) {
            // If the new frame is very close to 0, it's likely a loop restart (even for short videos)
            if (data.frame_index < 20) {
                // Accept as loop restart
                console.debug(`[useVideoSocket] Loop restart detected (prev=${lastProcessedIndexRef.current}, new=${data.frame_index})`);
                // Clear registry on loop to avoid stale ghosts from previous loop
                vehicleRegistryRef.current.clear();
                lastSeenVehicleTimeRef.current.clear();
            }
            // Otherwise, if the gap is small, it's likely just network jitter/out-of-order. Drop it.
            else if (lastProcessedIndexRef.current - data.frame_index < 100) {
                return;
            }
        }
        lastProcessedIndexRef.current = data.frame_index || 0;

        frameCountRef.current += 1;

        // --- Vehicle State Merging (Hydration) ---
        let now = performance.now();
        const mergedVehicles: VehicleFrontendData[] = [];
        const currentBatchIds = new Set<string>();

        if (data.vehicles && data.vehicles.length > 0) {
            data.vehicles.forEach((v: any) => {
                const vid = v.vehicle_id;
                currentBatchIds.add(vid);
                lastSeenVehicleTimeRef.current.set(vid, now);

                const existing = vehicleRegistryRef.current.get(vid);
                if (existing) {
                    // Merge new fields into existing state
                    const updated = { ...existing, ...v };
                    vehicleRegistryRef.current.set(vid, updated);
                    mergedVehicles.push(updated);
                } else {
                    // New vehicle
                    vehicleRegistryRef.current.set(vid, v as VehicleFrontendData);
                    mergedVehicles.push(v as VehicleFrontendData);
                }
            });
        }

        // Cleanup stale vehicles (not seen for > 2 seconds)
        for (const [vid, lastSeen] of lastSeenVehicleTimeRef.current.entries()) {
            if (now - lastSeen > 2000) {
                vehicleRegistryRef.current.delete(vid);
                lastSeenVehicleTimeRef.current.delete(vid);
            }
        }

        // Update metrics and vehicles state for side-panels (Throttled)
        
        // --- Annotation Persistence Logic ---
        // If we have new vehicle data, update the ref and timestamp
        if (mergedVehicles.length > 0) {
            lastVehiclesUpdateTimeRef.current = now;
        }

        if (now - lastStateUpdateRef.current > STATE_UPDATE_INTERVAL) {
            if (data.metrics) {
                // Only update metrics if they actually changed to avoid unnecessary re-renders
                setMetrics(prev => {
                    if (JSON.stringify(prev) === JSON.stringify(data.metrics)) return prev;
                    return data.metrics || null;
                });
            }
            
            // For vehicles, we use the merged batch. 
            // Only update the React state every 200ms.
            if (mergedVehicles.length > 0) {
                setVehicles(mergedVehicles);
            } else if (now - lastVehiclesUpdateTimeRef.current > ANNOTATION_PERSISTENCE_MS) {
                setVehicles(null);
            }
            
            lastStateUpdateRef.current = now;
        }

        let decodedImage: ImageBitmap | HTMLImageElement | null = null;

        if (data.frame instanceof ImageBitmap) {
            decodedImage = data.frame;
        } else if (data.frame instanceof ArrayBuffer || typeof data.frame === 'string') {
            try {
                let byteArray: Uint8Array;
                if (typeof data.frame === 'string') {
                    const byteString = atob(data.frame);
                    byteArray = new Uint8Array(byteString.length);
                    for (let i = 0; i < byteString.length; i++) {
                        byteArray[i] = byteString.charCodeAt(i);
                    }
                } else {
                    byteArray = new Uint8Array(data.frame);
                }

                const blob = new Blob([byteArray.buffer as BlobPart], { type: 'image/jpeg' });
                if ('createImageBitmap' in window) {
                    decodedImage = await createImageBitmap(blob);
                } else {
                    decodedImage = await new Promise((resolve, reject) => {
                        const img = new Image();
                        img.onload = () => resolve(img);
                        img.onerror = reject;
                        img.src = URL.createObjectURL(blob);
                    });
                }
            } catch (err) {
                console.error('[useVideoSocket] Main thread decoding fallback failed:', err);
                return;
            }
        }

        if (decodedImage) {
            // Guard against out-of-order execution: 
            // Only update if this frame is newer than what's currently in the ref
            if (data.frame_index !== undefined && frameRef.current && data.frame_index < frameRef.current.index) {
                // Exception for loop restarts: if new frame is < 20, we accept it as a restart
                if (data.frame_index >= 20) {
                    if (decodedImage instanceof ImageBitmap) decodedImage.close();
                    return;
                }
            }

            if (frameRef.current?.image instanceof ImageBitmap && frameRef.current.image !== decodedImage) {
                frameRef.current.image.close();
            }

            // Persistence for canvas drawing
            let vehiclesToStore = mergedVehicles.length > 0 ? mergedVehicles : null;
            if (!vehiclesToStore || vehiclesToStore.length === 0) {
                if (now - lastVehiclesUpdateTimeRef.current < ANNOTATION_PERSISTENCE_MS) {
                    vehiclesToStore = frameRef.current?.vehicles || null;
                }
            }

            frameRef.current = {
                image: decodedImage,
                index: data.frame_index || 0,
                vehicles: vehiclesToStore,
                metrics: data.metrics || null
            };
        }

        now = performance.now();
        if (lastFrameTimeRef.current > 0) {
            const frameTime = now - lastFrameTimeRef.current;
            if (smoothedFrameTimeRef.current === 0) {
                smoothedFrameTimeRef.current = frameTime;
            } else {
                smoothedFrameTimeRef.current = (FPS_EMA_ALPHA * frameTime) + ((1 - FPS_EMA_ALPHA) * smoothedFrameTimeRef.current);
            }

            if (now - lastFpsUpdateRef.current > 500 && smoothedFrameTimeRef.current > 0) {
                setFrameRate(1000 / smoothedFrameTimeRef.current);
                lastFpsUpdateRef.current = now;
            }
        }
        lastFrameTimeRef.current = now;
        lastSuccessfulFrameTimeRef.current = now;
    }, [streamId]);

    useEffect(() => {
        console.log(`[useVideoSocket] Mount/Update. streamId=${streamId}, token=${!!token}, isConnected=${client.isConnected()}`);
        if (!streamId || !token) {
            if (frameRef.current?.image instanceof ImageBitmap) {
                frameRef.current.image.close();
            }
            frameRef.current = null;
            setMetrics(null);
            setVehicles(null);
            setError(null);
            return;
        }

        if (client.isConnected()) {
            subscribeToFeed();
        }

        const unsubscribeFrame = client.subscribe(WebSocketMessageType.VIDEO_FRAME, handleFrame, streamId);

        const stalenessInterval = setInterval(() => {
            const now = performance.now();
            if (lastSuccessfulFrameTimeRef.current > 0 && 
                now - lastSuccessfulFrameTimeRef.current > FRAME_STALENESS_THRESHOLD) {
                if (frameRef.current) {
                    console.warn(`[useVideoSocket] Video stream for ${streamId} is stale (>5s). Clearing frame.`);
                    if (frameRef.current?.image instanceof ImageBitmap) {
                        frameRef.current.image.close();
                    }
                    frameRef.current = null;
                    setFrameRate(0);
                    setError('Video stream timed out. Waiting for new frames...');
                }
            }
        }, 1000);

        const unsubscribeStatus = client.onStatusChange((status, message) => {
            console.log(`[useVideoSocket] WebSocket status update: ${status}. Feed: ${streamId}. Client: ${client.getInstanceId()}`);
            const connected = status === 'connected';
            setIsConnected(connected);
            if (connected) {
                setError(null);
                subscribeToFeed();
            } else if (status === 'error' || status === 'disconnected') {
                setError(message || 'Video stream connection error.');
            }
        });

        return () => {
            if (client.isConnected() && streamId) {
                console.log(`[useVideoSocket] Unsubscribing from feed ${streamId}`);
                client.send({
                    type: WebSocketMessageType.UNSUBSCRIBE_FROM_FEED,
                    data: { feed_id: streamId },
                });
            }
            unsubscribeFrame();
            unsubscribeStatus();
            clearInterval(stalenessInterval);
            if (frameRef.current?.image instanceof ImageBitmap) {
                frameRef.current.image.close();
                frameRef.current = null;
            }
        };
    }, [client, streamId, token, subscribeToFeed, handleFrame]);

    const drawFrame = useCallback((
        ctx: CanvasRenderingContext2D, 
        frameDataObj: { image: ImageBitmap | HTMLImageElement | null, index: number, vehicles: VehicleFrontendData[] | null, metrics: SurveillanceFeedMessage | null }, 
        options: { 
            showBoundingBoxes?: boolean; 
            showVehicleDetails?: boolean; 
            showTrajectories?: boolean; 
            showLaneOverlays?: boolean;
            selectedVehicleIds?: Set<string>;
            showAllDetections?: boolean;
        } = {}
    ) => {
        const { image, index, vehicles: currentVehicles } = frameDataObj;
        if (!image) return;

        const { 
            showBoundingBoxes = true, 
            showVehicleDetails = true, 
            showTrajectories = true, 
            showLaneOverlays = false,
            selectedVehicleIds = new Set<string>(),
            showAllDetections = true
        } = options;

        const canvasWidth = ctx.canvas.width;
        const canvasHeight = ctx.canvas.height;
        const imgWidth = image.width;
        const imgHeight = image.height;

        // Detect loop or out-of-order frame
        if (index < lastDrawnIndexRef.current) {
            // If index is very low, assume loop restart regardless of gap size
            if (index < 20) {
                console.log(`[useVideoSocket] Video loop detected (index reset) for ${streamId}.`);
                lastDrawnIndexRef.current = -1;
            }
            // If the gap is large, it's likely a loop restart (for longer videos)
            else if (lastDrawnIndexRef.current - index > 100) {
                console.log(`[useVideoSocket] Video loop detected (large gap) for ${streamId}.`);
                lastDrawnIndexRef.current = -1;
            } else {
                // Minor jitter, just skip
                return;
            }
        }
        lastDrawnIndexRef.current = index;

        // Since we set canvas.width/height to imgWidth/height in SurveillanceFeed,
        // scaleX and scaleY should be 1. But for robustness, we use the actual ratios.
        const scaleX = imgWidth > 0 ? canvasWidth / imgWidth : 1;
        const scaleY = imgHeight > 0 ? canvasHeight / imgHeight : 1;

        ctx.drawImage(image, 0, 0, canvasWidth, canvasHeight);

        const vehiclesToDraw = currentVehicles || [];

        if (vehiclesToDraw.length > 0) {
            ctx.lineWidth = 1.5;
            ctx.font = 'bold 10px Arial'; // Slightly smaller font

            vehiclesToDraw.forEach(v => {
                if (v.status && v.status !== 'active' && v.status !== 'predicting' && v.status !== 'tentative') return;

                // Selective rendering logic:
                // If showAllDetections is false, only draw if vehicle is selected.
                const isSelected = selectedVehicleIds.has(v.vehicle_id) || (v.global_vehicle_id && selectedVehicleIds.has(v.global_vehicle_id));
                if (!showAllDetections && !isSelected) return;

                if (!v.bbox || !Array.isArray(v.bbox)) return; // Skip if no bbox (debounced or malformed)
                let [x1, y1, x2, y2] = v.bbox;

                if (!Number.isFinite(x1) || !Number.isFinite(y1) || !Number.isFinite(x2) || !Number.isFinite(y2)) {
                    return;
                }

                const sx1 = x1 * canvasWidth;
                const sy1 = y1 * canvasHeight;
                const sx2 = x2 * canvasWidth;
                const sy2 = y2 * canvasHeight;
                const sw = sx2 - sx1;
                const sh = sy2 - sy1;

                let color = '#888888'; // Default Gray
                switch (v.behavior) {
                    case 'moving': color = '#00FF00'; break;
                    case 'stopped': color = '#FF0000'; break;
                    case 'speeding': color = '#0000FF'; break;
                    case 'accelerating': color = '#FFFF00'; break;
                    case 'decelerating': color = '#00FFFF'; break;
                    case 'lane_changing': color = '#FF00FF'; break;
                }
                ctx.strokeStyle = color;

                // Visual distinction based on status
                if (v.status === 'predicting') {
                    ctx.setLineDash([2, 2]);
                    ctx.globalAlpha = 0.3;
                    ctx.lineWidth = 1;
                } else if (v.status === 'tentative') {
                    ctx.setLineDash([4, 4]);
                    ctx.globalAlpha = 0.6;
                    ctx.lineWidth = 1.2;
                } else if (v.is_wrong_way || v.is_stopped) {
                    const isEven = Math.floor(performance.now() / 200) % 2 === 0;
                    color = isEven ? '#FF0000' : '#FFFFFF';
                    ctx.setLineDash([]);
                    ctx.globalAlpha = 1.0;
                    ctx.lineWidth = 3;
                } else {
                    ctx.setLineDash([]);
                    ctx.globalAlpha = 1.0;
                    ctx.lineWidth = 2.0;
                }

                if (showBoundingBoxes) {
                    ctx.strokeRect(sx1, sy1, sw, sh);

                    // Trajectory Projection
                    if (showTrajectories && (Math.abs(v.vx ?? 0) > 0.0001 || Math.abs(v.vy ?? 0) > 0.0001)) {
                        const cx = sx1 + sw / 2;
                        const cy = sy1 + sh / 2;
                        const currentFps = frameRate > 0 ? frameRate : 15;
                        const projectionFrames = currentFps * 0.2;
                        const projX = cx + (v.vx ?? 0) * projectionFrames * canvasWidth;
                        const projY = cy + (v.vy ?? 0) * projectionFrames * canvasHeight;

                        ctx.beginPath();
                        ctx.moveTo(cx, cy);
                        ctx.lineTo(projX, projY);
                        ctx.lineWidth = 1;
                        ctx.setLineDash([4, 4]);
                        ctx.stroke();

                        const angle = Math.atan2(projY - cy, projX - cx);
                        const headLen = 6;
                        ctx.beginPath();
                        ctx.moveTo(projX, projY);
                        ctx.lineTo(projX - headLen * Math.cos(angle - Math.PI / 6), projY - headLen * Math.sin(angle - Math.PI / 6));
                        ctx.moveTo(projX, projY);
                        ctx.lineTo(projX - headLen * Math.cos(angle + Math.PI / 6), projY - headLen * Math.sin(angle + Math.PI / 6));
                        ctx.setLineDash([]);
                        ctx.stroke();
                    }

                    if (v.status === 'active') {
                        ctx.shadowBlur = 4;
                        ctx.shadowColor = color;
                        ctx.strokeRect(sx1, sy1, sw, sh);
                        ctx.shadowBlur = 0;
                    }
                }

                ctx.setLineDash([]);

                // Show labels for ACTIVE and TENTATIVE detections
                if (showVehicleDetails && (v.status === 'active' || v.status === 'tentative')) {
                    const speed = v.speed !== undefined && v.speed !== null ? v.speed.toFixed(0) : "0";
                    const vid = v.vehicle_id.split('_').pop();
                    const lines = [
                        `${vid}: ${v.class_name}`,
                        `${speed} km/h`
                    ];
                    if (v.license_plate && v.license_plate !== "Unknown") {
                        lines.push(v.license_plate);
                    }

                    const lineHeight = 12;
                    let textY = sy1 - (lines.length * lineHeight) - 4;
                    if (textY < 0) textY = sy2 + 4;

                    lines.forEach((line, i) => {
                        const textWidth = ctx.measureText(line).width;
                        const textX = sx1 + (sw - textWidth) / 2;
                        ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
                        ctx.fillRect(textX - 4, textY + (i * lineHeight), textWidth + 8, lineHeight);
                        ctx.fillStyle = (v.status === 'tentative') ? 'rgba(255,255,255,0.8)' : color;
                        ctx.fillText(line, textX, textY + (i * lineHeight) + 10);
                    });
                }
                ctx.globalAlpha = 1.0;
            });

            // Final style reset to prevent leaks to next frame/call
            ctx.globalAlpha = 1.0;
            ctx.setLineDash([]);
        }

        // --- Lane Calibration Overlays ---
        if (options.showLaneOverlays && frameDataObj.metrics?.calibration) {
            const calibration = frameDataObj.metrics.calibration as Record<string, { calibrated: boolean, confidence: number, consensus: [number, number] }>;

            ctx.save();
            ctx.font = 'bold 12px monospace';

            Object.entries(calibration).forEach(([laneId, data]) => {
                const lid = parseInt(laneId);
                if (lid === -1) return; // Skip global for overlays, or draw it in a corner

                // If not calibrated, we could draw "Calibrating..." but let's just focus on flow
                if (data.calibrated && data.consensus) {
                    // Try to find a vehicle in this lane to find where to draw the arrow
                    // Fallback to a grid if no vehicle
                    const laneVehicles = (frameDataObj.vehicles || []).filter(v => v.lane === lid);
                    let anchorX, anchorY;

                    if (laneVehicles.length > 0) {
                        const v = laneVehicles[0];
                        anchorX = (v.bbox[0] + v.bbox[2]) / 2 * (ctx.canvas.width / image.width);
                        anchorY = (v.bbox[1] + v.bbox[3]) / 2 * (ctx.canvas.height / image.height);
                    } else {
                        // Static positions based on lane ID (heuristic for visualization)
                        anchorX = (ctx.canvas.width / 4) * (lid % 3 + 1);
                        anchorY = (ctx.canvas.height / 4) * (Math.floor(lid / 3) + 1);
                    }

                    const [cvx, cvy] = data.consensus;
                    const arrowLen = 30;
                    const endX = anchorX + cvx * arrowLen;
                    const endY = anchorY + cvy * arrowLen;

                    ctx.strokeStyle = '#00FF00';
                    ctx.fillStyle = '#00FF00';
                    ctx.lineWidth = 2;
                    ctx.globalAlpha = 0.6;

                    // Draw Arrow
                    ctx.beginPath();
                    ctx.moveTo(anchorX, anchorY);
                    ctx.lineTo(endX, endY);
                    ctx.stroke();

                    const angle = Math.atan2(cvy, cvx);
                    ctx.beginPath();
                    ctx.moveTo(endX, endY);
                    ctx.lineTo(endX - 8 * Math.cos(angle - Math.PI / 6), endY - 8 * Math.sin(angle - Math.PI / 6));
                    ctx.lineTo(endX - 8 * Math.cos(angle + Math.PI / 6), endY - 8 * Math.sin(angle + Math.PI / 6));
                    ctx.closePath();
                    ctx.fill();

                    ctx.fillText(`L${lid}`, anchorX - 10, anchorY - 5);
                }
            });
            ctx.restore();
        }
    }, [streamId]);

    const updateFeedConfig = useCallback((config: any) => {
        if (client.isConnected() && streamId) {
            client.send({
                type: WebSocketMessageType.UPDATE_FEED_CONFIG,
                data: { feed_id: streamId, updates: config }
            });
        }
    }, [client, streamId]);

    return { lastFrameRef: frameRef, metrics, vehicles, isConnected, error, drawFrame, frameRate, updateFeedConfig };
};

export default useVideoSocket;
