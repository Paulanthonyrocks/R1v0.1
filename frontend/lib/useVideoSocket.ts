import { useState, useEffect, useCallback, useRef } from 'react';
import { WebSocketMessageType } from './websocket/WebSocketClient';
import { useWebSocket } from './websocket/WebSocketProvider';
import { SurveillanceFeedMessage, VideoFrameMessage } from './types'; // Removed MetricsUpdateMessage

// Define a type for a single vehicle for clarity in frontend
interface VehicleFrontendData {
    vehicle_id: string;
    bbox: [number, number, number, number];
    speed: number;
    license_plate: string;
    class_id: number;
    class_name: string;
    behavior: string;
    confidence: number;
    is_occluded: boolean;
    lane: number;
    status?: string;
}

const useVideoSocket = (streamId: string, token: string | null) => {
  const client = useWebSocket();
  const [frameData, setFrameData] = useState<Uint8Array | null>(null);
  const [metrics, setMetrics] = useState<SurveillanceFeedMessage | null>(null);
  const [vehicles, setVehicles] = useState<VehicleFrontendData[] | null>(null); // New state for vehicle data
  const [isConnected, setIsConnected] = useState(client.isConnected());
  const [error, setError] = useState<string | null>(null);
  const [frameRate, setFrameRate] = useState<number>(0);
  const lastFrameTimeRef = useRef<number>(0);
  const smoothedFrameTimeRef = useRef<number>(0); // Using EMA for frame time
  const FPS_EMA_ALPHA = 0.1; // Exponential Moving Average alpha. Lower is smoother, higher is more responsive.


  const frameCountRef = useRef<number>(0);

  const handleFrame = useCallback((data: VideoFrameMessage) => {
    // console.log(`[useVideoSocket] Raw data for ${streamId}:`, data.feed_id);
    if (data.feed_id && data.feed_id !== streamId) return;

    frameCountRef.current += 1;
    // Log every frame for debugging
    // console.log(`[useVideoSocket] Received frame #${frameCountRef.current} for ${streamId}. Data size: ${data.frame instanceof ArrayBuffer ? data.frame.byteLength : data.frame.length}`);
    
    if (data.metrics) {
        setMetrics(data.metrics);
    }
    if (data.vehicles) { // Store incoming vehicle data
        setVehicles(data.vehicles);
    } else {
        setVehicles(null); // Clear vehicles if none are sent (e.g., in no-detection frames)
    }

    let byteArray: Uint8Array;
    if (typeof data.frame === 'string') {
        const byteString = atob(data.frame);
        const byteNumbers = new Array(byteString.length);
        for (let i = 0; i < byteString.length; i++) {
            byteNumbers[i] = byteString.charCodeAt(i);
        }
        byteArray = new Uint8Array(byteNumbers);
    } else if (data.frame instanceof ArrayBuffer) {
        byteArray = new Uint8Array(data.frame);
    } else {
        return;
    }
    setFrameData(byteArray);

    const now = performance.now();
    if (lastFrameTimeRef.current > 0) {
        const frameTime = now - lastFrameTimeRef.current;

        // Use Exponential Moving Average to smooth frame time
        if (smoothedFrameTimeRef.current === 0) {
            smoothedFrameTimeRef.current = frameTime;
        } else {
            smoothedFrameTimeRef.current = (FPS_EMA_ALPHA * frameTime) + ((1 - FPS_EMA_ALPHA) * smoothedFrameTimeRef.current);
        }

        // Update FPS only if smoothedFrameTime is positive to avoid division by zero
        if (smoothedFrameTimeRef.current > 0) {
            setFrameRate(1000 / smoothedFrameTimeRef.current);
        }
    }
    lastFrameTimeRef.current = now;
  }, [streamId]);

  const drawFrame = useCallback((ctx: CanvasRenderingContext2D, frame: Uint8Array, currentVehicles: VehicleFrontendData[] | null, options: { showBoundingBoxes?: boolean; showVehicleDetails?: boolean } = {}) => {
    const { showBoundingBoxes = true, showVehicleDetails = true } = options;
    const blob = new Blob([frame as unknown as BlobPart], { type: 'image/jpeg' });

    // Helper to draw the image bitmap or image element
    const drawToCanvas = (imageSource: ImageBitmap | HTMLImageElement) => {
        const canvasWidth = ctx.canvas.width;
        const canvasHeight = ctx.canvas.height;
        const imgWidth = imageSource.width;
        const imgHeight = imageSource.height;

        // Calculate scale factors (protect against division by zero)
        const scaleX = imgWidth > 0 ? canvasWidth / imgWidth : 1;
        const scaleY = imgHeight > 0 ? canvasHeight / imgHeight : 1;

        ctx.drawImage(imageSource, 0, 0, canvasWidth, canvasHeight);
        
        // If it's a bitmap, close it to free memory
        if (imageSource instanceof ImageBitmap) {
            imageSource.close();
        }

        if (currentVehicles && currentVehicles.length > 0) {
            ctx.strokeStyle = 'red'; // Default color
            ctx.lineWidth = 1;
            ctx.font = '10px Arial';
            ctx.fillStyle = 'white';

            currentVehicles.forEach(v => {
                // Only draw active vehicles to prevent "sticky" ghost boxes
                if (v.status && v.status !== 'active') return;

                let [x1, y1, x2, y2] = v.bbox;
                
                // Validate bbox coordinates to prevent "random blotches" (rendering artifacts)
                if (!Number.isFinite(x1) || !Number.isFinite(y1) || !Number.isFinite(x2) || !Number.isFinite(y2)) {
                    return;
                }

                // Apply scaling to match canvas dimensions
                x1 *= scaleX;
                y1 *= scaleY;
                x2 *= scaleX;
                y2 *= scaleY;

                // Map behavior to color (simplified for frontend, can be expanded)
                let color = 'red'; // Default to red
                switch (v.behavior) {
                    case 'moving': color = 'lime'; break; // Green
                    case 'stopped': color = 'red'; break;
                    case 'speeding': color = 'blue'; break;
                    case 'accelerating': color = 'yellow'; break;
                    case 'decelerating': color = 'cyan'; break;
                    case 'lane_changing': color = 'magenta'; break;
                    default: color = 'gray'; break;
                }
                ctx.strokeStyle = color;

                if (showBoundingBoxes) {
                    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
                }
                
                if (showVehicleDetails) {
                    const lines = [
                        `ID: ${v.vehicle_id} (${v.class_name})`,
                        `Spd: ${v.speed.toFixed(1)} km/h`
                    ];
                    if (v.license_plate && v.license_plate !== "Unknown") {
                        lines.push(`LP: ${v.license_plate}`);
                    }

                    const font = `10px Arial`; 
                    ctx.font = font;

                    const lineHeight = 14; 
                    let textY = y1 - 3; // Start closer to bbox

                    // Adjust if text goes off screen top
                    if (textY < lines.length * lineHeight) {
                        textY = y2 + lineHeight;
                    }

                    lines.forEach((line, i) => {
                        const textWidth = ctx.measureText(line).width;
                        const textX = x1 + (x2 - x1 - textWidth) / 2; // Center horizontally

                        // Background for text
                        ctx.fillStyle = 'rgba(0, 0, 0, 0.6)'; // Semi-transparent black
                        ctx.fillRect(textX - 2, textY + (i * lineHeight) - lineHeight + 2, textWidth + 4, lineHeight);
                        
                        // Text
                        ctx.fillStyle = 'white';
                        ctx.fillText(line, textX, textY + (i * lineHeight) + 2);
                    });
                }
            });
        }
    };

    if ('createImageBitmap' in window) {
        createImageBitmap(blob).then(drawToCanvas).catch(e => {
            console.error('[useVideoSocket] createImageBitmap failed, falling back:', e);
            fallbackDraw();
        });
    } else {
        fallbackDraw();
    }

    function fallbackDraw() {
        const url = URL.createObjectURL(blob);
        const img = new Image();
        img.onload = () => {
            drawToCanvas(img);
            URL.revokeObjectURL(url);
        };
        img.onerror = (e) => {
            console.error('[useVideoSocket] Error loading image for drawing:', e);
            URL.revokeObjectURL(url);
        };
        img.src = url;
    }

  }, []); // Removed dependency on 'vehicles' as it is now passed as an argument

  // Removed handleMetrics callback, as metrics are now part of handleFrame

  useEffect(() => {
    if (!streamId || !token) return;

    const unsubscribeFrame = client.subscribe(WebSocketMessageType.VIDEO_FRAME, handleFrame);
    // Removed subscription to WebSocketMessageType.METRICS_UPDATE
    // const unsubscribeMetrics = client.subscribe(WebSocketMessageType.METRICS_UPDATE, handleMetrics);

    const subscribeToFeed = () => {
        console.log(`Subscribing to feed ${streamId}`);
        client.send({
            type: WebSocketMessageType.SUBSCRIBE_TO_FEED,
            data: { feed_id: streamId }
        });
    };

    if (client.isConnected()) {
        setIsConnected(true);
        subscribeToFeed();
    }

    const unsubscribeStatus = client.onStatusChange((status, message) => {
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
        console.log(`Unsubscribing from feed ${streamId}`);
        if (client.isConnected()) {
            client.send({
                type: WebSocketMessageType.UNSUBSCRIBE_FROM_FEED,
                data: { feed_id: streamId }
            });
        }
        unsubscribeFrame();
        // unsubscribeMetrics(); // Removed
        unsubscribeStatus();
    };
  }, [client, streamId, token, handleFrame]); // Removed handleMetrics from dependencies

  return { frameData, metrics, vehicles, isConnected, error, drawFrame, frameRate }; // Include vehicles in return
};

export default useVideoSocket;