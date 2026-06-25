import { useState, useEffect, useCallback, useRef } from 'react';
import { WebSocketMessageType, WebSocketMessage } from './websocket/WebSocketClient';
import { useWebSocket } from './websocket/WebSocketProvider';
import { SurveillanceFeedMessage, VideoFrameMessage, VehicleFrontendData, VideoFrameSnapshot } from './types';
import { useVideoDecoder } from './hooks/useVideoDecoder';
import videoStreamManager from './videoStreamManager';
import {
 _subscribedFeeds,
 _feedHookCounts,
 _pendingUnsubscribes,
 _pendingCleanups,
 UNSUBSCRIBE_DEBOUNCE_MS,
} from './websocket/feedSubscriptionState';

const useVideoSocket = (streamId: string, minimal: boolean = false) => {
  const hookId = useRef(Math.random().toString(36).substring(2, 7));
  const client = useWebSocket();
  
  // --- State Management ---
  const [isConnected, setIsConnected] = useState(() => client?.isConnected() ?? false);
  const [error, setError] = useState<string | null>(null);
  const [frameRate, setFrameRate] = useState<number>(0);
  
  // Consolidate vehicles and metrics to reduce re-render count
  const [feedState, setFeedState] = useState<{
    index: number,
    vehicles: VehicleFrontendData[] | null,
    metrics: SurveillanceFeedMessage | null
  }>({
    index: -1,
    vehicles: null,
    metrics: null
  });

  // --- Refs for High-Frequency Data ---
  const lastFrameRef = useRef<{
    image: ImageBitmap | HTMLImageElement | null,
    index: number,
    vehicles: VehicleFrontendData[] | null,
    metrics: SurveillanceFeedMessage | null
  } | null>(null);
  
  const vehiclesRef = useRef<VehicleFrontendData[] | null>(null);
  const metricsRef = useRef<SurveillanceFeedMessage | null>(null);

  const lastStateJsonRef = useRef<string>("");
  const throttleTimerRef = useRef<NodeJS.Timeout | null>(null);
  const UPDATE_INTERVAL_MS = 100; // 10 FPS max for UI updates

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
  const consecutiveStaleCountRef = useRef<number>(0);

  // Log only on actual mount/unmount to stop the console flood
  useEffect(() => {
    console.log(`[useVideoSocket ${hookId.current}] Hook mounted for streamId: ${streamId}`);
    return () => console.log(`[useVideoSocket ${hookId.current}] Hook unmounted for streamId: ${streamId}`);
  }, [streamId]);

  // Constants
  const STATE_UPDATE_INTERVAL = 500; 
  const FRAME_STALENESS_THRESHOLD = 15000; // 15 seconds - reduced from 60s to catch issues faster but avoid false positives
  const FPS_EMA_ALPHA = 0.1;

  // --- Sub-Hooks Implementation ---
  const { decode } = useVideoDecoder({
    onFrame: (decodedData) => {
      const { image, index, metrics: frameMetrics, vehicles: frameVehicles, timestamp } = decodedData;
      // Skip vehicle data in minimal mode to reduce memory/GC pressure
      const vehiclesToStore = minimal ? null : frameVehicles;
      
      if (index < (lastFrameRef.current?.index ?? -1)) {
        if (image instanceof ImageBitmap) image.close();
        return;
      }

      if (lastFrameRef.current?.image instanceof ImageBitmap && lastFrameRef.current.image !== image) {
        const oldImage = lastFrameRef.current.image;
        setTimeout(() => {
          try {
            oldImage.close();
          } catch (e) {}
        }, 100);
      }

      if (streamId) {
        if (!minimal) {
          videoStreamManager.updateVehicles(streamId, frameVehicles);
        }
        vehiclesRef.current = vehiclesToStore;
        metricsRef.current = frameMetrics;

        // Robust throttled state update for UI panels
        if (!throttleTimerRef.current) {
          throttleTimerRef.current = setTimeout(() => {
            throttleTimerRef.current = null;
            
            // Only trigger a re-render if a new frame has actually arrived
            if (index !== feedState.index) {
              setFeedState({
                index,
                vehicles: vehiclesToStore,
                metrics: frameMetrics
              });
            }
          }, UPDATE_INTERVAL_MS);
        }
      }

      lastFrameRef.current = {
        image,
        index,
        vehicles: vehiclesToStore,
        metrics: frameMetrics
      };

      const now = performance.now();
      if (lastFrameTimeRef.current > 0) {
        const frameTime = now - lastFrameTimeRef.current;
        smoothedFrameTimeRef.current = (FPS_EMA_ALPHA * frameTime) + ((1 - FPS_EMA_ALPHA) * smoothedFrameTimeRef.current || frameTime);
        
        // Throttle FPS updates to every 2 seconds and only if the integer part changes
        if (now - lastFpsUpdateRef.current > 2000) {
          const currentFps = 1000 / smoothedFrameTimeRef.current;
          if (Math.round(currentFps) !== Math.round(frameRate)) {
            setFrameRate(currentFps);
          }
          lastFpsUpdateRef.current = now;
        }
      }
      lastFrameTimeRef.current = now;
      lastSuccessfulFrameTimeRef.current = now;
    },
    onError: (err) => {
      console.error(`[useVideoSocket ${hookId.current}] Decoder Error:`, err);
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

  // Track recently processed frame_indexes per streamId to make handleFrame
  // idempotent. The same (feed_id, frame_index) tuple can reach this hook
  // multiple times during StrictMode double-mounts, HMR remounts, or when
  // the Web Worker emits the same decode twice (e.g. WebSocketClient.reconnect
  // flushes buffered frames while the worker still holds pending work).
  // Treating duplicate delivery as a no-op removes the visible
  // "DROPPING stale frame X < Y" flood that resurfaced when both the
  // pre-reconnect listener and the post-reconnect listener fired for the
  // same streamed frame.
  const recentlyProcessedRef = useRef<Set<number>>(new Set<number>());

  const handleFrame = useCallback(async (data: VideoFrameMessage) => {
      const currentStreamId = streamIdRef.current;

      if (data.frame_index !== undefined) {
        const seen = recentlyProcessedRef.current;
        if (seen.has(data.frame_index)) {
          // Same payload slipped in from two listeners; ignore the duplicate.
          return;
        }
        seen.add(data.frame_index);
        // Keep the dedup set bounded so it doesn't grow forever.
        if (seen.size > 128) {
          const arr = Array.from(seen).sort((a, b) => a - b);
          const drop = arr.slice(0, arr.length - 64);
          for (const idx of drop) seen.delete(idx);
        }

        // Detect feed restart/loop: if index drops significantly, reset the tracker
        if (lastProcessedIndexRef.current > 100 && data.frame_index < 10) {
          console.warn(`[useVideoSocket ${hookId.current}] Detected feed restart for ${currentStreamId}, resetting frame index tracker`);
          lastProcessedIndexRef.current = -1;
          seen.clear();
        }

        if (data.frame_index < lastProcessedIndexRef.current) {
          console.debug(`[useVideoSocket ${hookId.current}] DROPPING stale frame ${data.frame_index} < ${lastProcessedIndexRef.current} for ${currentStreamId}`);
          return;
        }
      }

      try {
        if (!data.feed_id) {
          console.warn(`[useVideoSocket ${hookId.current}] DROPPING frame without feed_id for ${currentStreamId}`);
          return;
        }
        if (data.feed_id !== currentStreamId) {
          console.error(`[useVideoSocket ${hookId.current}] CRITICAL: frame feed_id mismatch! Expected ${currentStreamId}, got ${data.feed_id}`);
          return;
        }
        if (data.frame_index !== undefined) {
        const lastIndex = lastProcessedIndexRef.current;
        if (lastIndex !== -1 && data.frame_index > lastIndex + 1) {
          const dropped = data.frame_index - lastIndex - 1;
          if (dropped > 30) {
            console.warn(`[useVideoSocket ${hookId.current}] Significant frame gap for ${currentStreamId}: ${dropped} frames`);
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
          metricsRef.current = data.metrics;
        }
        lastStateUpdateRef.current = now;
      }

      await decode(data);
    } catch (err) {
      console.error(`[useVideoSocket ${hookId.current}] handleFrame Error:`, err);
    }
  }, [client, decode]);

  // Sync the latest handleFrame to the ref
  useEffect(() => {
    handleFrameRef.current = handleFrame;
  }, [handleFrame]);

  // --- Lifecycle Management ---

  useEffect(() => {
    if (!streamId) return;

    lastProcessedIndexRef.current = -1;
    const currentCount = _feedHookCounts.get(streamId) ?? 0;
    _feedHookCounts.set(streamId, currentCount + 1);

    if (client?.isConnected()) {
      subscribeToFeed();
    }

    console.log(`[useVideoSocket ${hookId.current}] Mounting hook for streamId: ${streamId}. Subscribing to VIDEO_FRAME...`);
    const currentStreamId = streamId;
    const unsubscribeFrame = client?.subscribe(
    WebSocketMessageType.VIDEO_FRAME, 
    (frameData: VideoFrameMessage) => {
 if (!frameData.feed_id) {
   console.warn(`[useVideoSocket ${hookId.current}] DROPPING frame with missing feed_id for ${currentStreamId}.`);
   if (frameData.frame instanceof ImageBitmap) {
     frameData.frame.close();
   }
   return;
 }
 if (frameData && frameData.frame) {
 if (frameData.feed_id !== currentStreamId) {
 console.warn(`[useVideoSocket ${hookId.current}] HARD GUARD DROP: expected ${currentStreamId}, got ${frameData.feed_id}`);
 if (frameData.frame instanceof ImageBitmap) {
   frameData.frame.close();
 }
 return;
 }
 console.debug(`[useVideoSocket ${hookId.current}] ACCEPTED frame for ${currentStreamId} (index: ${frameData.frame_index})`);
 handleFrameRef.current?.(frameData);
 }
 }, 
 streamId
 );

    const stalenessInterval = setInterval(() => {
      const now = performance.now();
      const timeSinceLastFrame = now - lastSuccessfulFrameTimeRef.current;
      
      if (lastSuccessfulFrameTimeRef.current > 0 && 
          timeSinceLastFrame > FRAME_STALENESS_THRESHOLD) {
        // Require 3 consecutive stale checks before showing error (prevents flicker on brief hiccups)
        consecutiveStaleCountRef.current += 1;
        
        if (consecutiveStaleCountRef.current >= 3) {
          // Only show error after sustained outage
          if (lastFrameRef.current?.image instanceof ImageBitmap) {
            lastFrameRef.current.image.close();
          }
          lastFrameRef.current = null;
          setFrameRate(0);
          setError('Video stream timed out.');
        }
      } else {
        // Reset counter if we get a fresh frame
        consecutiveStaleCountRef.current = 0;
        if (error) setError(null); // Clear error automatically when frames resume
      }
    }, 5000); // Check every 5 seconds instead of every second

    const unsubscribeStatus = client?.onStatusChange((status: string) => {
      setIsConnected(status === 'connected' || status === 'authenticated');
      if (status === 'authenticated') {
        const pending = _pendingUnsubscribes.get(streamId);
        if (pending) {
          clearTimeout(pending);
          _pendingUnsubscribes.delete(streamId);
        }
        _subscribedFeeds.delete(streamId);
        subscribeToFeed();
      } else if (status === 'disconnected') {
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
    frame: {
      image: ImageBitmap | HTMLImageElement | null;
      index: number;
      vehicles: VehicleFrontendData[] | null;
      metrics: SurveillanceFeedMessage | null;
    },
    options: any = {}
  ) => {
    const { image, vehicles, metrics } = frame;
    const {
      showBoundingBoxes = true,
      showVehicleDetails = true,
      showTrajectories = false,
      selectedVehicleIds = new Set(),
      showAllDetections = false,
      minimal = false,  // Dashboard thumbnails: skip per-vehicle bboxes/metrics canvas text
    } = options;

    ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);

    if (image) {
      // ImageBitmap can be closed/detached after decoder reuses memory
      const isDetached = image instanceof ImageBitmap && !image.width && !image.height;
      if (!isDetached) {
        try {
          ctx.drawImage(image, 0, 0, ctx.canvas.width, ctx.canvas.height);
        } catch (e) {
          // Fallback for race conditions where bitmap detaches mid-check
          console.warn(`[useVideoSocket ${hookId.current}] Failed to draw frame - image detached`);
        }
      }
    }

    // Skip expensive per-vehicle drawing on dashboard thumbnails
    if (minimal) return;

    if (vehicles && showBoundingBoxes && vehicles.length > 0) {
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

        if (bbox && Array.isArray(bbox) && bbox.length === 4) {
          const [x1, y1, x2, y2] = bbox;
          const width = (x2 - x1) * ctx.canvas.width;
          const height = (y2 - y1) * ctx.canvas.height;
          const x = x1 * ctx.canvas.width;
          const y = y1 * ctx.canvas.height;

          if (width > 0 && height > 0) {
            const isSelected = selectedVehicleIds.has(global_vehicle_id || vehicle_id);

            ctx.strokeStyle = isSelected ? '#00ff00' : '#ff0000';
            ctx.lineWidth = isSelected ? 3 : 2;
            ctx.strokeRect(x, y, width, height);

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

    if (metrics && showVehicleDetails) {
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
    metrics: feedState.metrics, 
    vehicles: feedState.vehicles, 
    isConnected, 
    error, 
    drawFrame, 
    frameRate, 
    updateFeedConfig: (config: any) => client?.send({ type: WebSocketMessageType.UPDATE_FEED_CONFIG, data: { feed_id: streamId, updates: config } })
  };
};

export default useVideoSocket;