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
  const frameTimesRef = useRef<number[]>([]); // To store last N frame durations
  const FPS_SMOOTHING_WINDOW_SIZE = 10; // Number of frames to average over

  const handleFrame = useCallback((data: VideoFrameMessage) => {
    if (data.feed_id && data.feed_id !== streamId) return;

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
        frameTimesRef.current.push(frameTime);
        if (frameTimesRef.current.length > FPS_SMOOTHING_WINDOW_SIZE) {
            frameTimesRef.current.shift(); // Remove oldest frame time
        }

        const averageFrameTime = frameTimesRef.current.reduce((a, b) => a + b, 0) / frameTimesRef.current.length;
        setFrameRate(1000 / averageFrameTime);
    }
    lastFrameTimeRef.current = now;
  }, [streamId]);

  const drawFrame = useCallback((ctx: CanvasRenderingContext2D, frame: Uint8Array, options: { showBoundingBoxes?: boolean; showVehicleDetails?: boolean } = {}) => {
    const { showBoundingBoxes = true, showVehicleDetails = true } = options;
    const blob = new Blob([frame as unknown as BlobPart], { type: 'image/jpeg' });
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = () => {
        ctx.drawImage(img, 0, 0, ctx.canvas.width, ctx.canvas.height);
        URL.revokeObjectURL(url);

        if (vehicles && vehicles.length > 0) { // Use the new 'vehicles' state
            ctx.strokeStyle = 'red'; // Default color
            ctx.lineWidth = 2;
            ctx.font = '12px Arial';
            ctx.fillStyle = 'white';

            vehicles.forEach(v => {
                const [x1, y1, x2, y2] = v.bbox;

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

                    const fontScale = 0.5; // From backend's visualization_options
                    const font = `${fontScale * 24}px Arial`; // Convert scale to px, adjust as needed
                    ctx.font = font;

                    const lineHeight = 18; // Approx line height
                    let textY = y1 - 5; // Start above bbox

                    // Adjust if text goes off screen top
                    if (textY < lines.length * lineHeight) {
                        textY = y2 + lineHeight;
                    }

                    lines.forEach((line, i) => {
                        const textWidth = ctx.measureText(line).width;
                        const textX = x1 + (x2 - x1 - textWidth) / 2; // Center horizontally

                        // Background for text
                        ctx.fillStyle = 'rgba(0, 0, 0, 0.6)'; // Semi-transparent black
                        ctx.fillRect(textX - 4, textY + (i * lineHeight) - lineHeight + 2, textWidth + 8, lineHeight);
                        
                        // Text
                        ctx.fillStyle = 'white';
                        ctx.fillText(line, textX, textY + (i * lineHeight) + 2);
                    });
                }
            });
        }
    };
    img.src = url;
  }, [vehicles]); // Dependency on 'vehicles'

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