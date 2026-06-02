/**
 * Video Worker — decodes inbound JPEG frames into ImageBitmaps.
 *
 * Design goals:
 *  1. Skip stale frames — if a new frame arrives while we're processing
 *     the previous one, cancel the old decode and start fresh.
 *  2. Use AbortController for cancellable createImageBitmap.
 *  3. Fast-path: if the backend sends a raw ArrayBuffer (binary msgpack),
 *     decode directly. Fallback: base64 → Blob → ImageBitmap.
 *  4. Only process ONE frame at a time per feed. Drop intermediate frames.
 */

// Per-feed state
const canvases = new Map();
const contexts = new Map();
const pendingDecodes = new Map(); // feed_id → AbortController

// Track latest pending frame per feed (coalescing)
const latestFramePayload = new Map(); // feed_id → { binaryFrame, frameData, metadata }

self.onmessage = async function (e) {
    const data = e.data;
    const command = data.command;
    const command_feed_id = data.feed_id;

    // ── Command: Cleanup ────────────────────────────────────────────
    if (command === 'CLEANUP_FEED') {
        // Abort any pending decode for this feed
        const existing = pendingDecodes.get(command_feed_id);
        if (existing) {
            existing.abort();
            pendingDecodes.delete(command_feed_id);
        }
        latestFramePayload.delete(command_feed_id);

        const canvas = canvases.get(command_feed_id);
        if (canvas) canvases.delete(command_feed_id);
        const ctx = contexts.get(command_feed_id);
        if (ctx) contexts.delete(command_feed_id);
        return;
    }

    // ── Normal frame ────────────────────────────────────────────────
    const binaryFrame = data.binaryFrame;
    const frameData = data.frameData;
    const feed_id = data.feed_id;

    if (!feed_id) return;

    // Coalesce: if we're already processing a frame for this feed,
    // cancel that decode and store the latest payload.
    const existingAbort = pendingDecodes.get(feed_id);
    if (existingAbort) {
        existingAbort.abort();
        pendingDecodes.delete(feed_id);
    }

    // ── Extract raw JPEG bytes ─────────────────────────────────────
    let jpegBytes = null;
    let metadata = {};

    if (binaryFrame) {
        // Modern binary msgpack path - align with backend short keys
        metadata = {
            frame_index: binaryFrame.i || 0,
            metrics: binaryFrame.m,
            vehicles: binaryFrame.v,
            timestamp: binaryFrame.ts
        };

        // The binary frame bytes are in the 'bg' key
        jpegBytes = binaryFrame.bg; 
    } else if (frameData && typeof frameData === 'string') {
        // Legacy base64 path — fast decode using Fetch + Data URL
        jpegBytes = frameData; // Will be converted below
    }

    if (!jpegBytes) {
        // No decodable payload — post empty frame to signal "no content"
        self.postMessage({
            feed_id: feed_id,
            frame: null,
            frame_index: metadata.frame_index || 0,
            metrics: metadata.metrics,
            vehicles: metadata.vehicles,
            timestamp: metadata.timestamp
        });
        return;
    }

    // ── Decode JPEG → ImageBitmap ───────────────────────────────────
    const abortController = new AbortController();
    pendingDecodes.set(feed_id, abortController);

    try {
        let blob;

        if (jpegBytes instanceof ArrayBuffer || jpegBytes instanceof Uint8Array) {
            blob = new Blob([jpegBytes], { type: 'image/jpeg' });
        } else if (typeof jpegBytes === 'string') {
            // base64 → Blob via data URL (2-4x faster than atob+Uint8Array loop)
            const response = await fetch('data:image/jpeg;base64,' + jpegBytes, {
                signal: abortController.signal
            });
            if (!response.ok) throw new Error('Failed to decode base64 JPEG');
            blob = await response.blob();
        }

        // Check if this decode was aborted while awaiting blob/fetch
        if (abortController.signal.aborted) return;

        const bitmap = await createImageBitmap(blob, {
            imageOrientation: 'none',
            premultiplyAlpha: 'none',
            colorSpaceConversion: 'none'
        });

        // Check again after createImageBitmap
        if (abortController.signal.aborted) {
            bitmap.close();
            return;
        }

        self.postMessage({
            feed_id: feed_id,
            frame: bitmap,
            frame_index: metadata.frame_index || 0,
            metrics: metadata.metrics,
            vehicles: metadata.vehicles,
            timestamp: metadata.timestamp
        }, [bitmap]); // Transfer ownership to main thread
    } catch (error) {
        if (error.name === 'AbortError') {
            // Frame was superseded — expected, not an error
            return;
        }
        console.error('Worker: Frame processing failed', error);
        self.postMessage({
            error: error.message,
            feed_id: feed_id,
            frame_index: metadata.frame_index || 0
        });
    } finally {
        // Only clear if this abort controller is still the current one
        if (pendingDecodes.get(feed_id) === abortController) {
            pendingDecodes.delete(feed_id);
        }
    }
};