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
  const FPS_EMA_ALPHA = 0.1;

  const frameCountRef = useRef<number>(0);
  const lastDrawnIndexRef = useRef<number>(-1);
  const lastProcessedIndexRef = useRef<number>(-1);

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
    if (data.feed_id && data.feed_id !== streamId) {
        return;
    }

    // Drop late frames (out of order), but allow for loops
    if (data.frame_index !== undefined && data.frame_index < lastProcessedIndexRef.current) {
        if (lastProcessedIndexRef.current - data.frame_index < 100) {
            return;
        }
    }
    lastProcessedIndexRef.current = data.frame_index || 0;

    frameCountRef.current += 1;
    
    // Update metrics state for side-panels
    if (data.metrics) {
        setMetrics(data.metrics);
    }
    if (data.vehicles) {
        setVehicles(data.vehicles);
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
            if (decodedImage instanceof ImageBitmap) decodedImage.close();
            return;
        }

        if (frameRef.current?.image instanceof ImageBitmap && frameRef.current.image !== decodedImage) {
            frameRef.current.image.close();
        }
        
        frameRef.current = { 
            image: decodedImage, 
            index: data.frame_index || 0,
            vehicles: data.vehicles || null,
            metrics: data.metrics || null
        };
    }

    const now = performance.now();
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
  }, [streamId]);

  useEffect(() => {
    console.log(`[useVideoSocket] Mount/Update. streamId=${streamId}, token=${!!token}, isConnected=${client.isConnected()}`);
    if (!streamId || !token) {
        if (frameRef.current?.image instanceof ImageBitmap) {
            frameRef.current.image.close();
        }
        frameRef.current = null;
        setMetrics(null);
        setError(null);
        return;
    }

    if (client.isConnected()) {
      subscribeToFeed();
    }

    const unsubscribeFrame = client.subscribe(WebSocketMessageType.VIDEO_FRAME, handleFrame, streamId);

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
      if (frameRef.current?.image instanceof ImageBitmap) {
          frameRef.current.image.close();
          frameRef.current = null;
      }
    };
  }, [client, streamId, token, subscribeToFeed, handleFrame]);

  const drawFrame = useCallback((ctx: CanvasRenderingContext2D, frameDataObj: { image: ImageBitmap | HTMLImageElement | null, index: number, vehicles: VehicleFrontendData[] | null }, options: { showBoundingBoxes?: boolean; showVehicleDetails?: boolean } = {}) => {
    const { image, index, vehicles: currentVehicles } = frameDataObj;
    if (!image) return;

    const { showBoundingBoxes = true, showVehicleDetails = true } = options;

    const canvasWidth = ctx.canvas.width;
    const canvasHeight = ctx.canvas.height;
    const imgWidth = image.width;
    const imgHeight = image.height;

    // Detect loop or out-of-order frame
    if (index < lastDrawnIndexRef.current) {
        // If the gap is large, it's likely a loop restart
        if (lastDrawnIndexRef.current - index > 100) {
            console.log(`[useVideoSocket] Video loop detected for ${streamId}. Resetting frame tracker.`);
            lastDrawnIndexRef.current = -1;
            smoothedVehiclesRef.current.clear();
        } else {
            // Minor jitter, just skip
            return;
        }
    }
    lastDrawnIndexRef.current = index;

    const scaleX = imgWidth > 0 ? canvasWidth / imgWidth : 1;
    const scaleY = imgHeight > 0 ? canvasHeight / imgHeight : 1;

    ctx.drawImage(image, 0, 0, canvasWidth, canvasHeight);
    
    const vehiclesToDraw = currentVehicles || [];
    
    if (vehiclesToDraw.length > 0) {
        ctx.lineWidth = 2;
        ctx.font = 'bold 10px Arial';

        vehiclesToDraw.forEach(v => {
            if (v.status && v.status !== 'active') return;

            let [x1, y1, x2, y2] = v.bbox;
            
            if (!Number.isFinite(x1) || !Number.isFinite(y1) || !Number.isFinite(x2) || !Number.isFinite(y2)) {
                return;
            }

            const sx1 = x1 * scaleX;
            const sy1 = y1 * scaleY;
            const sx2 = x2 * scaleX;
            const sy2 = y2 * scaleY;
            const sw = sx2 - sx1;
            const sh = sy2 - sy1;

            let color = '#FF0000';
            switch (v.behavior) {
                case 'moving': color = '#00FF00'; break; // Bright lime
                case 'stopped': color = '#FF0000'; break; // Red
                case 'speeding': color = '#0000FF'; break; // Blue
                case 'accelerating': color = '#FFFF00'; break; // Yellow
                case 'decelerating': color = '#00FFFF'; break; // Cyan
                case 'lane_changing': color = '#FF00FF'; break; // Magenta
                default: color = '#888888'; break; // Gray
            }
            ctx.strokeStyle = color;

            if (showBoundingBoxes) {
                ctx.strokeRect(sx1, sy1, sw, sh);
                // Add a small inner glow/shadow effect
                ctx.strokeStyle = 'rgba(0,0,0,0.5)';
                ctx.lineWidth = 1;
                ctx.strokeRect(sx1 + 1, sy1 + 1, sw - 2, sh - 2);
                ctx.lineWidth = 2;
                ctx.strokeStyle = color;
            }
            
            if (showVehicleDetails) {
                const lines = [
                    `${v.vehicle_id.split('_').pop()}: ${v.class_name}`,
                    `${v.speed.toFixed(0)} km/h`
                ];
                if (v.license_plate && v.license_plate !== "Unknown") {
                    lines.push(v.license_plate);
                }

                const lineHeight = 12; 
                let textY = sy1 - (lines.length * lineHeight) - 2;

                if (textY < 0) {
                    textY = sy2 + 2;
                }

                lines.forEach((line, i) => {
                    const textWidth = ctx.measureText(line).width;
                    const textX = sx1 + (sw - textWidth) / 2;

                    ctx.fillStyle = 'rgba(0, 0, 0, 0.7)';
                    ctx.fillRect(textX - 2, textY + (i * lineHeight), textWidth + 4, lineHeight);
                    
                    ctx.fillStyle = color;
                    ctx.fillText(line, textX, textY + (i * lineHeight) + 10);
                });
            }
        });
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