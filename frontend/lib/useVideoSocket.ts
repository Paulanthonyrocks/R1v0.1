import { useState, useEffect, useCallback, useRef } from 'react';
import { WebSocketMessageType } from './websocket/WebSocketClient';
import { useWebSocket } from './websocket/WebSocketProvider';
import { SurveillanceFeedMessage, VideoFrameMessage } from './types';

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
  const [vehicles, setVehicles] = useState<VehicleFrontendData[] | null>(null);
  const [isConnected, setIsConnected] = useState(client.isConnected());
  const [error, setError] = useState<string | null>(null);
  const [frameRate, setFrameRate] = useState<number>(0);
  const lastFrameTimeRef = useRef<number>(0);
  const smoothedFrameTimeRef = useRef<number>(0);
  const FPS_EMA_ALPHA = 0.1;

  const frameCountRef = useRef<number>(0);

  useEffect(() => {
    if (!streamId || !token) return;

    const subscribeToFeed = () => {
      client.send({
        type: WebSocketMessageType.SUBSCRIBE_TO_FEED,
        data: { feed_id: streamId },
      });
    };

    if (client.isConnected()) {
      subscribeToFeed();
    }

    const unsubscribe = () => {
      if (client.isConnected()) {
        client.send({
          type: WebSocketMessageType.UNSUBSCRIBE_FROM_FEED,
          data: { feed_id: streamId },
        });
      }
    };

    return () => {
      unsubscribe();
    };
  }, [client, streamId, token]);

  const handleFrame = useCallback((data: VideoFrameMessage) => {
    if (data.feed_id && data.feed_id !== streamId) return;

    frameCountRef.current += 1;
    
    if (data.metrics) {
        setMetrics(data.metrics);
    }
    if (data.vehicles) {
        setVehicles(data.vehicles);
    } else {
        setVehicles(null);
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
        if (smoothedFrameTimeRef.current === 0) {
            smoothedFrameTimeRef.current = frameTime;
        } else {
            smoothedFrameTimeRef.current = (FPS_EMA_ALPHA * frameTime) + ((1 - FPS_EMA_ALPHA) * smoothedFrameTimeRef.current);
        }
        if (smoothedFrameTimeRef.current > 0) {
            setFrameRate(1000 / smoothedFrameTimeRef.current);
        }
    }
    lastFrameTimeRef.current = now;
  }, [streamId]);

  const drawFrame = useCallback((ctx: CanvasRenderingContext2D, frame: Uint8Array, currentVehicles: VehicleFrontendData[] | null, options: { showBoundingBoxes?: boolean; showVehicleDetails?: boolean } = {}) => {
    const { showBoundingBoxes = true, showVehicleDetails = true } = options;
    const blob = new Blob([frame as unknown as BlobPart], { type: 'image/jpeg' });

    const drawToCanvas = (imageSource: ImageBitmap | HTMLImageElement) => {
        const canvasWidth = ctx.canvas.width;
        const canvasHeight = ctx.canvas.height;
        const imgWidth = imageSource.width;
        const imgHeight = imageSource.height;

        const scaleX = imgWidth > 0 ? canvasWidth / imgWidth : 1;
        const scaleY = imgHeight > 0 ? canvasHeight / imgHeight : 1;

        ctx.drawImage(imageSource, 0, 0, canvasWidth, canvasHeight);
        
        if (imageSource instanceof ImageBitmap) {
            imageSource.close();
        }

        if (currentVehicles && currentVehicles.length > 0) {
            ctx.strokeStyle = 'red';
            ctx.lineWidth = 1;
            ctx.font = '10px Arial';
            ctx.fillStyle = 'white';

            currentVehicles.forEach(v => {
                if (v.status && v.status !== 'active') return;

                let [x1, y1, x2, y2] = v.bbox;
                
                if (!Number.isFinite(x1) || !Number.isFinite(y1) || !Number.isFinite(x2) || !Number.isFinite(y2)) {
                    return;
                }

                x1 *= scaleX;
                y1 *= scaleY;
                x2 *= scaleX;
                y2 *= scaleY;

                let color = 'red';
                switch (v.behavior) {
                    case 'moving': color = 'lime'; break;
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
                    let textY = y1 - 3;

                    if (textY < lines.length * lineHeight) {
                        textY = y2 + lineHeight;
                    }

                    lines.forEach((line, i) => {
                        const textWidth = ctx.measureText(line).width;
                        const textX = x1 + (x2 - x1 - textWidth) / 2;

                        ctx.fillStyle = 'rgba(0, 0, 0, 0.6)';
                        ctx.fillRect(textX - 2, textY + (i * lineHeight) - lineHeight + 2, textWidth + 4, lineHeight);
                        
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

  }, []);

  useEffect(() => {
    if (!streamId || !token) return;

    const unsubscribeFrame = client.subscribe(WebSocketMessageType.VIDEO_FRAME, handleFrame);

    const unsubscribeStatus = client.onStatusChange((status, message) => {
        const connected = status === 'connected';
        setIsConnected(connected);
        if (connected) {
            setError(null);
        } else if (status === 'error' || status === 'disconnected') {
            setError(message || 'Video stream connection error.');
        }
    });

    return () => {
        unsubscribeFrame();
        unsubscribeStatus();
    };
  }, [client, streamId, token, handleFrame]);

  return { frameData, metrics, vehicles, isConnected, error, drawFrame, frameRate };
};

export default useVideoSocket;
