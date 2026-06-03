import { useState, useEffect, useCallback, useRef } from 'react';
import { WebSocketMessageType, WebSocketMessage } from './websocket/WebSocketClient';
import { useWebSocket } from './websocket/WebSocketProvider';
import { SurveillanceFeedMessage, VideoFrameMessage, VehicleFrontendData } from './types';
import { useVideoDecoder } from './hooks/useVideoDecoder';
import videoStreamManager from './videoStreamManager';
import {
 _subscribedFeeds,
 _feedHookCounts,
 _pendingUnsubscribes,
 _pendingCleanups,
 UNSUBSCRIBE_DEBOUNCE_MS,
} from './websocket/feedSubscriptionState';

const useVideoSocket = (streamId: string) => {
  console.log(`[useVideoSocket] Hook initializing for streamId: ${streamId}`);
  const client = useWebSocket();
  
  // --- State Management ---
  const [metrics, setMetrics] = useState<SurveillanceFeedMessage | null>(null);
  const [vehicles, setVehicles] = useState<VehicleFrontendData[] | null>(null);
  const [isConnected, setIsConnected] = useState(() => client?.isConnected() ?? false);
  const [error, setError] = useState<string | null>(null);
  const [frameRate, setFrameRate] = useState<number>(0);

  // --- Refs for High-Frequency Data ---
  const lastFrameRef = useRef<{
    image: ImageBitmap | HTMLImageElement | null,
    index: number,
    vehicles: VehicleFrontendData[] | null,
    metrics: SurveillanceFeedMessage | null
  } | null>(null);
  
  const lastFrameTimeRef = useRef<number>(0);
  const smoothedFrameTimeRef = useRef<number>(0);
  const lastFpsUpdateRef = useRef<number>(0);
  const lastStateUpdateRef = useRef<number>(0);
  const lastSuccessfulFrameTimeRef = useRef<number>(performance.now());
  const lastProcessedIndexRef = useRef<number>(-1);
  const frameCountRef = useRef<number>(0);
  const lastDrawnIndexRef = useRef<number>(-1);
  const handleFrameRef = useRef<((data: VideoFrameMessage) => Promise<void>) | null>(null);
  const frameClosureTimeoutRef = useRef<NodeJS.Timeout | null>(null);

  // Constants
  const STATE_UPDATE_INTERVAL = 200;
  const FRAME_STALENESS_THRESHOLD = 60000;
  const FPS_EMA_ALPHA = 0.1;

  // --- Sub-Hooks Implementation ---
  const { decode } = useVideoDecoder({
    onFrame: (decodedData) => {
      const { image, index, metrics, vehicles, timestamp } = decodedData;
      if (index < (lastFrameRef.current?.index ?? -1)) {
        if (image instanceof ImageBitmap) image.close();
        return;
      }

      if (lastFrameRef.current?.image instanceof ImageBitmap && lastFrameRef.current.image !== image) {
        const oldImage = lastFrameRef.current.image;
        setTimeout(() => {
          try {
            oldImage.close();
          } catch (e) {
            // Ignore if already closed
          }
        }, 100); // Delay closure to ensure the render loop has drawn the frame
      }

      if (streamId) {
        videoStreamManager.updateVehicles(streamId, vehicles);
        if (videoStreamManager.shouldUpdateUI(streamId)) {
          setVehicles(videoStreamManager.getVehicles(streamId));
        }
        // Clear any previous error state upon successful frame receipt
        setError(null);
      }

      lastFrameRef.current = {
        image,
        index,
        vehicles,
        metrics
      };

      const now = performance.now();
      if (lastFrameTimeRef.current > 0) {
        const frameTime = now - lastFrameTimeRef.current;
        smoothedFrameTimeRef.current = (FPS_EMA_ALPHA * frameTime) + ((1 - FPS_EMA_ALPHA) * smoothedFrameTimeRef.current || frameTime);
        if (now - lastFpsUpdateRef.current > 500) {
          setFrameRate(1000 / smoothedFrameTimeRef.current);
          lastFpsUpdateRef.current = now;
        }
      }
      lastFrameTimeRef.current = now;
      lastSuccessfulFrameTimeRef.current = now;
    },
    onError: (err) => {
      console.error('[useVideoSocket] Decoder Error:', err);
      setError(err.message);
    }
  });

  // --- Core Logic ---

  const streamIdRef = useRef(streamId);
  useEffect(() => {
    streamIdRef.current = streamId;
  }, [streamId]);

  const subscribeToFeed = useCallback(() => {
    const streamIdLocal = streamIdRef.current;
    if (client?.isConnected() && streamIdLocal) {
      const pending = _pendingUnsubscribes.get(streamIdLocal);
      if (pending) {
        clearTimeout(pending);
        _pendingUnsubscribes.delete(streamIdLocal);
      }
      const pendingCleanup = _pendingCleanups.get(streamIdLocal);
      if (pendingCleanup) {
        clearTimeout(pendingCleanup);
        _pendingCleanups.delete(streamIdLocal);
      }
      if (!_subscribedFeeds.has(streamIdLocal)) {
        _subscribedFeeds.add(streamIdLocal);
        client?.send({
          type: WebSocketMessageType.SUBSCRIBE_TO_FEED,
          data: { feed_id: streamIdLocal },
        });
      }
    }
  }, [client]);

const unsubscribeFromFeed = useCallback(() => {
    const streamIdLocal = streamIdRef.current;
    const count = _feedHookCounts.get(streamIdLocal) ?? 0;
    if (count === 0 && _subscribedFeeds.has(streamIdLocal)) {
      const existing = _pendingUnsubscribes.get(streamIdLocal);
      if (existing) clearTimeout(existing);

      const timer = setTimeout(() => {
        _pendingUnsubscribes.delete(streamIdLocal);
        if (!_subscribedFeeds.has(streamIdLocal)) return;
        _subscribedFeeds.delete(streamIdLocal);
        client?.send({
          type: WebSocketMessageType.UNSUBSCRIBE_FROM_FEED,
          data: { feed_id: streamIdLocal },
        });
      }, UNSUBSCRIBE_DEBOUNCE_MS);

      _pendingUnsubscribes.set(streamIdLocal, timer);
    }
  }, [client]);

  const handleFrame = useCallback(async (data: VideoFrameMessage) => {
      // Strict Monotonicity: If we receive a frame older than the last one we processed,
      // drop it immediately. This prevents "rewind" juggling.
      if (data.frame_index !== undefined && data.frame_index < lastProcessedIndexRef.current) {
        console.debug(`[useVideoSocket] DROPPING stale frame ${data.frame_index} < ${lastProcessedIndexRef.current} for ${streamId}`);
        return;
      }

      try {
        if (!data.feed_id) {
          console.warn(`[useVideoSocket.handleFrame] DROPPING frame without feed_id for ${streamId}`);
          return;
        }
        if (data.feed_id !== streamId) {
          console.error(`[useVideoSocket.handleFrame] CRITICAL: frame feed_id mismatch! Expected ${streamId}, got ${data.feed_id}`);
          return;
        }
        if (data.frame_index !== undefined) {
        const lastIndex = lastProcessedIndexRef.current;
        if (lastIndex !== -1 && data.frame_index > lastIndex + 1) {
          const dropped = data.frame_index - lastIndex - 1;
          if (dropped > 30) {
            console.warn(`[useVideoSocket] Significant frame gap for ${streamId}: ${dropped} frames`);
          }
        }
        lastProcessedIndexRef.current = data.frame_index;
      } else {
        lastProcessedIndexRef.current = (lastProcessedIndexRef.current || 0) + 1;
      }
      frameCountRef.current += 1;

      const now = performance.now();

      if (now - lastStateUpdateRef.current > STATE_UPDATE_INTERVAL) {
        if (data.metrics) {
          const metricsData = data.metrics;
          setMetrics((prev: SurveillanceFeedMessage | null) => (prev && prev.timestamp === metricsData.timestamp) ? prev : (metricsData ?? null));
        }
        lastStateUpdateRef.current = now;
      }

      await decode(data);
    } catch (err) {
      console.error('[useVideoSocket] handleFrame Error:', err);
    }
  }, [client, streamId, decode]);

  // Sync the latest handleFrame to the ref
  useEffect(() => {
    handleFrameRef.current = handleFrame;
  }, [handleFrame]);

  // --- Lifecycle Management ---

  useEffect(() => {
    if (!streamId) return;

    const currentCount = _feedHookCounts.get(streamId) ?? 0;
    _feedHookCounts.set(streamId, currentCount + 1);

    if (client?.isConnected()) {
      subscribeToFeed();
    }

    console.log(`[useVideoSocket] Mounting hook for streamId: ${streamId}. Subscribing to VIDEO_FRAME...`);
    const currentStreamId = streamId;
    const unsubscribeFrame = client?.subscribe(
    WebSocketMessageType.VIDEO_FRAME, 
    (frameData: VideoFrameMessage) => {
 // Note: the worker posts data directly, not wrapped in WebSocketMessage.
 // frameData = { feed_id, frame: ImageBitmap, frame_index, metrics, vehicles, timestamp }
 if (!frameData.feed_id) {
   console.warn(`[useVideoSocket] DROPPING frame with missing feed_id. Data keys: ${Object.keys(frameData || {}).join(',')}`);
   // Close the ImageBitmap if present to prevent memory leak
   if (frameData.frame instanceof ImageBitmap) {
     frameData.frame.close();
   }
   return;
 }
 if (frameData && frameData.frame) {
 // Hard guard: reject frames not matching our subscribed feed.
 // This prevents stale-closure leaks if the scoped routing
 // ever delivers a mismatched frame (e.g. during reconnect).
 if (frameData.feed_id !== currentStreamId) {
 console.warn(`[useVideoSocket] HARD GUARD DROP: expected ${currentStreamId}, got ${frameData.feed_id}`);
 // Close the ImageBitmap to prevent memory leak
 if (frameData.frame instanceof ImageBitmap) {
   frameData.frame.close();
 }
 return;
 }
 console.debug(`[useVideoSocket] ACCEPTED frame for ${currentStreamId} (index: ${frameData.frame_index})`);
 handleFrameRef.current?.(frameData);
 }
 }, 
 streamId
 );

    const stalenessInterval = setInterval(() => {
      const now = performance.now();
      if (lastSuccessfulFrameTimeRef.current > 0 && 
          now - lastSuccessfulFrameTimeRef.current > FRAME_STALENESS_THRESHOLD) {
        if (lastFrameRef.current?.image instanceof ImageBitmap) {
          lastFrameRef.current.image.close();
        }
        lastFrameRef.current = null;
        setFrameRate(0);
        setError('Video stream timed out.');
      }
    }, 1000);

    const unsubscribeStatus = client?.onStatusChange((status: string) => {
      setIsConnected(status === 'connected' || status === 'authenticated');
      if (status === 'authenticated') {
        // When reconnecting, cancel any pending unsubscribes so they don't
        // fire after the re-subscribe. Do NOT call resetFeedSubscriptionState()
        // because that wipes _subscribedFeeds which the subscribe logic checks.
        const pending = _pendingUnsubscribes.get(streamId);
        if (pending) {
          clearTimeout(pending);
          _pendingUnsubscribes.delete(streamId);
        }
        _subscribedFeeds.delete(streamId);
        subscribeToFeed();
      } else if (status === 'disconnected') {
        // Cancel pending unsubscribes — don't clear subscription state because
        // the server still tracks our subscription and will restore it on reconnect.
        const pending = _pendingUnsubscribes.get(streamId);
        if (pending) {
          clearTimeout(pending);
          _pendingUnsubscribes.delete(streamId);
        }
      }
    });

    return () => {
      const count = _feedHookCounts.get(streamId) ?? 1;
      if (count <= 1) {
        _feedHookCounts.delete(streamId);
      } else {
        _feedHookCounts.set(streamId, count - 1);
      }
      
      unsubscribeFromFeed();
      if (count <= 1) {
        const existingCleanup = _pendingCleanups.get(streamId);
        if (existingCleanup) clearTimeout(existingCleanup);

        const cleanupTimer = setTimeout(() => {
          _pendingCleanups.delete(streamId);
          client?.cleanupWorkerResources(streamId);
        }, UNSUBSCRIBE_DEBOUNCE_MS);

        _pendingCleanups.set(streamId, cleanupTimer);
      }
      if (unsubscribeFrame) unsubscribeFrame();
      if (unsubscribeStatus) unsubscribeStatus();
      clearInterval(stalenessInterval);
      if (frameClosureTimeoutRef.current) {
        clearTimeout(frameClosureTimeoutRef.current);
      }
      if (lastFrameRef.current?.image instanceof ImageBitmap) {
        lastFrameRef.current.image.close();
      }
    };
  }, [client, streamId, subscribeToFeed, unsubscribeFromFeed]);

  // --- Public API ---

  const drawFrame = useCallback((
    ctx: CanvasRenderingContext2D,
    frameDataObj: { image: ImageBitmap | HTMLImageElement | null, index: number, vehicles: VehicleFrontendData[] | null, metrics: SurveillanceFeedMessage | null },
    options: any = {}
  ) => {
    const { image, vehicles, metrics } = frameDataObj;
    const {
      showBoundingBoxes = true,
      showVehicleDetails = true,
      showTrajectories = false,
      selectedVehicleIds = new Set(),
      showAllDetections = false
    } = options;

    // Clear canvas
    ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);

    // Draw video frame if available
    if (image) {
      try {
        ctx.drawImage(image, 0, 0, ctx.canvas.width, ctx.canvas.height);
      } catch (e) {
        console.warn('[useVideoSocket] Failed to draw frame - image likely detached', e);
      }
    }

    // Draw vehicle detections if available
    if (vehicles && showBoundingBoxes && vehicles.length > 0) {
      // Filter vehicles based on showAllDetections setting
      const vehiclesToShow = showAllDetections ? vehicles : vehicles.filter(v => selectedVehicleIds.has(v.global_vehicle_id || v.vehicle_id));

      vehiclesToShow.forEach(vehicle => {
        const {
          bbox,
          class_name,
          speed,
          license_plate,
          behavior,
          is_wrong_way,
          is_stopped,
          global_vehicle_id,
          vehicle_id
        } = vehicle;

        // Draw bounding box
        if (bbox && Array.isArray(bbox) && bbox.length === 4) {
          const [x1, y1, x2, y2] = bbox;
          const width = (x2 - x1) * ctx.canvas.width;
          const height = (y2 - y1) * ctx.canvas.height;
          const x = x1 * ctx.canvas.width;
          const y = y1 * ctx.canvas.height;

          // Only draw if we have a valid size
          if (width > 0 && height > 0) {
            const isSelected = selectedVehicleIds.has(global_vehicle_id || vehicle_id);

            // Set style based on selection and vehicle properties
            ctx.strokeStyle = isSelected ? '#00ff00' : '#ff0000';
            ctx.lineWidth = isSelected ? 3 : 2;
            ctx.strokeRect(x, y, width, height);

            // Draw vehicle details if enabled
            if (showVehicleDetails) {
              ctx.fillStyle = isSelected ? 'rgba(0, 255, 0, 0.3)' : 'rgba(255, 0, 0, 0.3)';
              ctx.font = '12px monospace';
              ctx.fillText(`${class_name} ${speed.toFixed(1)} km/h`, x, y - 10);
              if (license_plate) {
                ctx.fillText(license_plate, x, y + height + 15);
              }
            }
          }
        }
      });
    }

    // Draw metrics if available
    if (metrics && showVehicleDetails) {
      // Draw basic metrics on canvas
      ctx.fillStyle = '#00ff00';
      ctx.font = '14px monospace';
      if (metrics.total_vehicles !== undefined) {
        ctx.fillText(`Vehicles: ${metrics.total_vehicles}`, 10, 20);
      }
      if (metrics.average_speed_kmh !== undefined) {
        ctx.fillText(`Speed: ${metrics.average_speed_kmh} km/h`, 10, 40);
      }
    }
  }, []); 

  return { 
    lastFrameRef, 
    metrics, 
    vehicles, 
    isConnected, 
    error, 
    drawFrame, 
    frameRate, 
    updateFeedConfig: (config: any) => client?.send({ type: WebSocketMessageType.UPDATE_FEED_CONFIG, data: { feed_id: streamId, updates: config } })
  };
};

export default useVideoSocket;
