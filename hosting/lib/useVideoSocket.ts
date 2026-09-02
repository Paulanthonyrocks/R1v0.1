import { useState, useEffect, useCallback, useRef } from 'react';
import { WebSocketMessageType, WebSocketMessage } from './websocket/WebSocketClient';
import { useWebSocket } from './websocket/WebSocketProvider';
import { SurveillanceFeedMessage, VideoFrameMessage, VehicleFrontendData, VideoFrameSnapshot, LaneOverlayData } from './types';
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
    metrics: SurveillanceFeedMessage | null,
    lanes?: LaneOverlayData | null
  } | null>(null);
  
  const vehiclesRef = useRef<VehicleFrontendData[] | null>(null);
  const metricsRef = useRef<SurveillanceFeedMessage | null>(null);
  // Sticky lane geometry: skip-frames carry no "ln" payload, so keep the last
  // known lanes here to avoid flicker on the lane-flow overlay between
  // detect frames.
  const lanesRef = useRef<LaneOverlayData | null>(null);

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
  // TELEMETRY: per-feed frame counter sampled on the 5s staleness tick so we
  // can see exact delivery rate without flooding the console. Reset each tick
  // so the printed number is "frames since last tick" -> "fps" when divided by 5.
  const framesSinceLastTickRef = useRef<number>(0);
  const lastDrawnIndexRef = useRef<number>(-1);
  const handleFrameRef = useRef<((data: VideoFrameMessage) => Promise<void>) | null>(null);
  const consecutiveStaleCountRef = useRef<number>(0);
  // Holds the previous ImageBitmap waiting to be closed. The renderer's rAF
  // loop notifies us via this ref once the new frame has been painted so we
  // don't close a bitmap the canvas is actively showing.
  const pendingClosureRef = useRef<ImageBitmap | null>(null);

  // Log only on actual mount/unmount to stop the console flood
  useEffect(() => {
    console.log(`[useVideoSocket ${hookId.current}] Hook mounted for streamId: ${streamId}`);
    return () => console.log(`[useVideoSocket ${hookId.current}] Hook unmounted for streamId: ${streamId}`);
  }, [streamId]);

  // Constants
  const STATE_UPDATE_INTERVAL = 500;
  // A frame GAP (backend backpressure / per-batch dedup) is normal and does NOT
  // mean the stream died. We must NOT blank the canvas or show "unavailable"
  // just because decoding stalled for a few seconds -- the last good frame should
  // stay painted (paused) and resume when frames return. Only a *sustained*
  // outage (no decoded frame for >=15s: 10s threshold + 2 consecutive 5s ticks)
  // warrants the unavailable overlay. Tightened from 30s+3 because under heavy
  // tunnel backpressure + low-priority deque drops, the old window let the
  // overlay lag the actual freeze by ~90s, which made the user think the
  // frontend was the problem when in fact the backend's bounded deque was
  // already silently dropping all incoming frames.
  const FRAME_STALENESS_THRESHOLD = 10000; // 10s of total silence before flagging stale
  const STALE_ERROR_AFTER = 2; // require 2 consecutive stale ticks (10s+5s=15s) to show error
  const FPS_EMA_ALPHA = 0.1;
  // Backward jump in frame_index larger than this means the source looped (or
  // the feed restarted), not an out-of-order/stale frame. Every monotonic
  // guard in the receive path must reset on it, otherwise the first loop
  // permanently wedges that guard. Shared by handleFrame's
  // lastProcessedIndexRef gate and the decoder onFrame gate.
  const LOOP_RESET_BACKJUMP = 100;

  // --- Sub-Hooks Implementation ---
  const { decode } = useVideoDecoder({
    onFrame: (decodedData) => {
      const { image, index, metrics: frameMetrics, vehicles: frameVehicles, lanes: frameLanes, timestamp } = decodedData;
      // AUDIT FIX (2026-08-24): minimal mode used to NULL out vehicle data to save
      // GC pressure — but that stripped bboxes from every grid tile and thumbnail
      // permanently (drawFrame draws boxes from frame.vehicles). The expensive part
      // of overlays is per-vehicle label text + metrics readout, which drawFrame
      // already gates via skipLabels=minimal. Vehicle arrays are small
      // (<= max_detections_per_frame), so keep them and let boxes render everywhere.
      const vehiclesToStore = frameVehicles;
      if (frameLanes) lanesRef.current = frameLanes;
      
      const previousIndex = lastFrameRef.current?.index ?? -1;
      if (index < previousIndex) {
        // A looped source wraps frame_index back toward 0. The upstream guards
        // (video-worker's highestAcceptedIndex, handleFrame's
        // lastProcessedIndexRef) BOTH reset their watermark on a large backward
        // jump, but this one used to have no reset at all -- so after the first
        // loop every post-wrap frame was dropped here forever. The canvas froze
        // on the last pre-loop frame and lastSuccessfulFrameTimeRef stopped
        // advancing, which tripped the staleness watchdog into
        // "VIDEO FEED UNAVAILABLE" while ACCEPTED frames kept climbing in the
        // console. Mirror the same backjump reset so a loop flows through.
        if (previousIndex > LOOP_RESET_BACKJUMP && index < previousIndex - LOOP_RESET_BACKJUMP) {
          console.debug(`[useVideoSocket ${hookId.current}] Loop wrap at decoder (prev=${previousIndex}, new=${index}); accepting new sequence`);
        } else {
          if (image instanceof ImageBitmap) {
            try { image.close(); } catch (e) {}
          }
          return;
        }
      }

      // Skip redundant decoding if same frame received (reconnect race)
      if (image === lastFrameRef.current?.image) {
        return;
      }

      // Defer closing the previous bitmap until the rAF render loop has had a
      // chance to paint the NEW frame at least once. Without this, a burst of
      // frames during a "Significant frame gap" recovery can race close the
      // currently-displayed bitmap in the 100ms window before the canvas
      // redraws, leaving a black/blank canvas for one or more paint cycles.
      const previousImage = lastFrameRef.current?.image instanceof ImageBitmap &&
        lastFrameRef.current.image !== image
        ? lastFrameRef.current.image
        : null;
      if (previousImage) {
        // Mark the new image so we know to close the *previous* one once the
        // renderer has drawn it. The rAF loop watches lastDrawnIndex and
        // signals settle via pendingClosureRef.
        pendingClosureRef.current = previousImage;
      }

      if (streamId) {
        videoStreamManager.updateVehicles(streamId, frameVehicles);
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
        metrics: frameMetrics,
        lanes: lanesRef.current
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

        // Detect feed restart/loop: if index jumps significantly BACKWARD,
        // reset the tracker.
        //
        // IMPORTANT: a *seamless* loop on a looped source file also wraps
        // frame_index back toward 0 with NO gap in frame delivery (the backend
        // keeps streaming; only the index resets). A *genuine* backend restart
        // (crash/respawn) leaves the feed SILENT for seconds first. We use that
        // delivery gap to tell the two apart so a normal video loop is not
        // misreported as a feed crash -- which previously spammed the console
        // and made a real restart indistinguishable from a loop.
        //
        // We must key off a large BACKWARD jump (last - incoming > threshold),
        // NOT "incoming < 10". Under backend frame-skipping the first post-loop
        // frame can arrive as index 10/25/50 (0-9 skipped); requiring < 10 then
        // never fires, lastProcessedIndex stays pinned high, and every wrapped
        // frame is dropped as stale forever -> the stream hangs while frames
        // keep arriving.
        if (
          lastProcessedIndexRef.current > LOOP_RESET_BACKJUMP &&
          data.frame_index < lastProcessedIndexRef.current - LOOP_RESET_BACKJUMP
        ) {
          const timeSinceLastFrame = performance.now() - lastSuccessfulFrameTimeRef.current;
          // 5s (was 2s): the 2026-08-16 run showed ordinary stall gaps up to
          // ~9.5s on tunnel clients while the backend stayed healthy, and a
          // 2033ms silence tripped a false "feed restart" alarm. A genuine
          // backend restart still surfaces as backend-side process death, so
          // nothing is lost by widening the window.
          const isGenuineRestart = timeSinceLastFrame > 5000;
          if (isGenuineRestart) {
            console.warn(`[useVideoSocket ${hookId.current}] Detected feed restart for ${currentStreamId} (silent for ${Math.round(timeSinceLastFrame)}ms), resetting frame index tracker`);
          } else {
            console.debug(`[useVideoSocket ${hookId.current}] Loop detected for ${currentStreamId} (frame_index wrapped to ${data.frame_index}), resetting tracker without restart alert`);
          }
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
      // TELEMETRY: count frames-per-5s window for the staleness tick below.
      framesSinceLastTickRef.current += 1;

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
      // TELEMETRY: print per-feed delivery rate every 5s so we can see exactly
      // when frames stop arriving. fps = frames_in_5s / 5. Includes the gap
      // since the last successful frame so a stall is visible immediately.
      const framesInWindow = framesSinceLastTickRef.current;
      framesSinceLastTickRef.current = 0;
      const deliveryFps = (framesInWindow / 5).toFixed(2);
      const lastIdx = lastProcessedIndexRef.current;
      console.debug(`[useVideoSocket ${hookId.current}] delivery-check feed=${currentStreamId} fps_in_last_5s=${deliveryFps} last_index=${lastIdx} gap_ms=${lastSuccessfulFrameTimeRef.current > 0 ? Math.round(timeSinceLastFrame) : 'n/a'}`);
      
      if (lastSuccessfulFrameTimeRef.current > 0 && 
          timeSinceLastFrame > FRAME_STALENESS_THRESHOLD) {
        // Require N consecutive stale checks before showing error (prevents flicker
        // on brief hiccups AND on the routine frame gaps the backend produces
        // under load). Until then we simply leave the last good frame painted.
        consecutiveStaleCountRef.current += 1;
        
        if (consecutiveStaleCountRef.current >= STALE_ERROR_AFTER) {
          // Only show error after a *sustained* outage. NOTE: we intentionally do
          // NOT null lastFrameRef or close its bitmap here -- the canvas should
          // keep showing the last frame (paused) rather than going blank, and a
          // gap does not mean the stream is gone.
          setFrameRate(0);
          setError('Video stream timed out.');
        }
      } else {
        // Fresh frame arrived: reset the stale counter and clear any stale error
        // immediately. A received frame means the stream is alive, so the
        // "unavailable" overlay must not linger after a gap recovers.
        consecutiveStaleCountRef.current = 0;
        if (error) setError(null);
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
      if (throttleTimerRef.current) {
        clearTimeout(throttleTimerRef.current);
        throttleTimerRef.current = null;
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
      lanes?: LaneOverlayData | null;
    },
    options: any = {}
  ) => {
    const { image, vehicles, metrics, lanes } = frame;
    const {
      showBoundingBoxes = true,
      showVehicleDetails = true,
      showTrajectories = false,
      selectedVehicleIds = new Set(),
      showAllDetections = false,
      showLaneOverlays = false,
    } = options;

    // ImageBitmap can be closed/detached after decoder reuses memory. If we
    // detect a detached image, DO NOT clear the canvas — that would blank out
    // the previously painted frame for one or more paint cycles during a frame
    // burst (the original "blank before rerender" symptom). Instead, skip the
    // wipe entirely; the next successful non-detached frame will draw normally
    // (drawImage already overwrites the prior content if needed).
    const isDetached = image instanceof ImageBitmap && !image.width && !image.height;
    if (isDetached) {
      return;
    }

    ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);

    if (image) {
      try {
        ctx.drawImage(image, 0, 0, ctx.canvas.width, ctx.canvas.height);
      } catch (e) {
        // Fallback for race conditions where bitmap detaches mid-draw
        console.warn(`[useVideoSocket ${hookId.current}] Failed to draw frame - image detached`);
      }
    }

    // Lane-flow overlay: draw the backend's normalized lane lines/bounds
    // (payload key "ln"). Gated by the StreamOverlayControls toggle.
    if (showLaneOverlays && lanes) {
      const cw = ctx.canvas.width;
      const ch = ctx.canvas.height;
      ctx.save();
      if (Array.isArray(lanes.lines)) {
        ctx.strokeStyle = 'rgba(0, 255, 255, 0.45)';
        ctx.lineWidth = 2;
        for (const line of lanes.lines) {
          const [x1, y1, x2, y2] = line;
          ctx.beginPath();
          ctx.moveTo(x1 * cw, y1 * ch);
          ctx.lineTo(x2 * cw, y2 * ch);
          ctx.stroke();
        }
      }
      if (Array.isArray(lanes.bounds) && lanes.bounds.length >= 2) {
        ctx.strokeStyle = 'rgba(0, 255, 255, 0.18)';
        ctx.setLineDash([4, 4]);
        ctx.beginPath();
        for (const bx of lanes.bounds) {
          const px = bx * cw;
          ctx.moveTo(px, 0);
          ctx.lineTo(px, ch);
        }
        ctx.stroke();
        ctx.setLineDash([]);
      }
      ctx.restore();
    }

    // Minimal/thumbnail mode used to `return` here and skip ALL overlay
    // drawing, which made bounding boxes vanish on the surveillance grid and
    // dashboard tiles. We now keep drawing boxes everywhere (cheap). Labels and
    // the corner metrics readout are gated by the "Vehicle Details (Labels)"
    // toggle (showVehicleDetails), so it works in grid/thumbnail view too.
    const skipLabels = !showVehicleDetails;

    if (vehicles && (showBoundingBoxes || showVehicleDetails) && vehicles.length > 0) {
      // Color system: box outline = class-type color, overridden by safety state
      // (wrong-way / stopped) and selection; a `predicting` (missed-frame hold) or
      // `tentative` box is dashed/dimmed so a real car is visually distinct from a
      // stale "ghost" hold. Labels get a dark chip, white bold text, a class swatch
      // and a speed-band-colored speed readout for visibility over the camera image.
      const CLASS_COLORS: Record<string, string> = {
        car: '#00e5ff', truck: '#ff9f43', bus: '#ffd93d',
        motorcycle: '#6ee7b7', bicycle: '#6ee7b7', person: '#f6ad55',
      };
      const clsColor = (cn?: string) => CLASS_COLORS[(cn || '').toLowerCase()] || '#f1f5f9';
      const speedColor = (s: number) =>
        (s < 15 ? '#ff6b6b' : s < 40 ? '#ffd93d' : s < 70 ? '#4ade80' : '#38bdf8');
      const boxColor = (v: VehicleFrontendData, sel: boolean) =>
        v.is_wrong_way ? '#d946ef' : v.is_stopped ? '#fb923c' : sel ? '#00ff00' : clsColor(v.class_name);

      // When nothing is selected, show every tracked vehicle so the feed is
      // never blank on first load. Once the operator selects a vehicle AND
      // "Show All Detections" is OFF, narrow the view to the selection only.
      const vehiclesToShow = showAllDetections
        ? vehicles
        : vehicles.filter(v => {
            const id = v.global_vehicle_id || v.vehicle_id;
            return selectedVehicleIds.size === 0 || selectedVehicleIds.has(id);
          });

      vehiclesToShow.forEach(vehicle => {
        const {
          bbox,
          class_name,
          speed,
          license_plate,
          global_vehicle_id,
          vehicle_id,
          status,
        } = vehicle;

        if (bbox && Array.isArray(bbox) && bbox.length === 4) {
          const [x1, y1, x2, y2] = bbox;
          const width = (x2 - x1) * ctx.canvas.width;
          const height = (y2 - y1) * ctx.canvas.height;
          const x = x1 * ctx.canvas.width;
          const y = y1 * ctx.canvas.height;

          if (width > 0 && height > 0) {
            const isSelected = selectedVehicleIds.has(global_vehicle_id || vehicle_id);
            const isPredicting = status === 'predicting';
            const isTentative = status === 'tentative';

            if (showBoundingBoxes) {
              ctx.save();
              ctx.strokeStyle = boxColor(vehicle, isSelected);
              ctx.lineWidth = isSelected ? 3 : 2;
              if (isPredicting || isTentative) {
                ctx.setLineDash(isPredicting ? [6, 4] : [2, 3]);
                ctx.globalAlpha = isTentative ? 0.4 : 0.7;
              }
              ctx.strokeRect(x, y, width, height);
              ctx.restore();
            }

            if (showVehicleDetails) {
              const spd = (typeof speed === 'number' && Number.isFinite(speed)) ? speed : 0;
              const clsTxt = (class_name || '?').toUpperCase();
              const spdTxt = `${spd.toFixed(0)} km/h`;
              // Label chip (class name + speed) above the box, clamped on-canvas.
              ctx.save();
              ctx.font = 'bold 12px monospace';
              const clsW = ctx.measureText(clsTxt).width;
              const spdW = ctx.measureText(spdTxt).width;
              const chipW = clsW + 6 + spdW + 12;
              const lx = Math.max(2, Math.min(x, ctx.canvas.width - chipW - 2));
              const ly = Math.max(2, y - 22);
              ctx.fillStyle = 'rgba(0, 0, 0, 0.72)';
              ctx.fillRect(lx, ly, chipW, 16);
              // class swatch
              ctx.fillStyle = boxColor(vehicle, isSelected);
              ctx.fillRect(lx, ly, 4, 16);
              ctx.fillStyle = '#ffffff';
              ctx.fillText(clsTxt, lx + 8, ly + 12);
              // speed in its band color
              ctx.fillStyle = speedColor(spd);
              ctx.fillText(spdTxt, lx + 8 + clsW + 6, ly + 12);
              ctx.restore();

              if (license_plate && license_plate !== 'Unknown' && license_plate !== '') {
                ctx.save();
                ctx.font = 'bold 11px monospace';
                const pw = ctx.measureText(license_plate).width;
                const px2 = Math.max(2, Math.min(x, ctx.canvas.width - pw - 10));
                const py2 = Math.min(ctx.canvas.height - 18, y + height + 6);
                ctx.fillStyle = 'rgba(0, 0, 0, 0.72)';
                ctx.fillRect(px2, py2, pw + 10, 15);
                ctx.fillStyle = '#ffffff';
                ctx.fillText(license_plate, px2 + 8, py2 + 11);
                ctx.restore();
              }
            }
          }
        }
      });
    }

    if (metrics && showVehicleDetails) {
      ctx.fillStyle = '#00e5ff';
      ctx.font = 'bold 14px monospace';
      if (metrics.total_vehicles !== undefined) {
        ctx.fillText(`Vehicles: ${metrics.total_vehicles}`, 10, 20);
      }
      if (metrics.average_speed_kmh !== undefined) {
        ctx.fillText(`Speed: ${metrics.average_speed_kmh} km/h`, 10, 40);
      }
    }
  }, []);

  // Called by the rAF render loop after it has drawn the current frame to
  // the canvas. We close the queued "previous" ImageBitmap now so it can be
  // GC'd without leaving the canvas pointing at a detached bitmap.
  const notifyFrameRendered = useCallback((drawnIndex: number) => {
    if (
      pendingClosureRef.current &&
      lastDrawnIndexRef.current === drawnIndex
    ) {
      const toClose = pendingClosureRef.current;
      pendingClosureRef.current = null;
      // Defer close by a microtask so the drawImage call has fully completed
      // on the GPU side before memory is released.
      setTimeout(() => {
        try {
          toClose.close();
        } catch (e) {}
      }, 0);
    }
    lastDrawnIndexRef.current = drawnIndex;
  }, []);

  return {
    lastFrameRef,
    metrics: feedState.metrics,
    vehicles: feedState.vehicles,
    isConnected,
    error,
    drawFrame,
    frameRate,
    notifyFrameRendered,
    updateFeedConfig: (config: any) => client?.send({ type: WebSocketMessageType.UPDATE_FEED_CONFIG, data: { feed_id: streamId, updates: config } })
  };
};

export default useVideoSocket;