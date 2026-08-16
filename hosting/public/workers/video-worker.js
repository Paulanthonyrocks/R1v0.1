/**
 * Video Worker — decodes inbound JPEG frames into ImageBitmaps.
 *
 * Design goals:
 *  1. Coalesce frames: If multiple frames arrive before the previous one is decoded,
 *     only the latest one is processed.
 *  2. Strict feed isolation: Each feed has its own processing state to prevent "juggling".
 *  3. Memory efficiency: Use ImageBitmap and transfer ownership to the main thread.
 *  4. Priority: Use binary msgpack path as primary, base64 as fallback.
 *  5. Monotonic decode: track the highest frame_index accepted per feed so
 *     out-of-order arrivals (rare, but observed during HMR remount of the
 *     WebSocketClient) cannot bypass coalescence with an older frame_index.
 */

importScripts('msgpack.min.js');

// State tracking per feed
const processingState = new Map(); // feed_id → { isProcessing: boolean, latestPayload: null | object, highestAcceptedIndex: number }

/**
 * Accept-or-reject an incoming payload based on monotonic frame_index.
 * Older indexes are silently dropped to prevent stale-frame surfacing on
 * the main thread. When the gap implies a loop/restart we reset the
 * watermark so the new sequence can flow through.
 */
function shouldAccept(feed_id, frame_index) {
    if (typeof frame_index !== 'number' || frame_index < 0) return true;
    const state = processingState.get(feed_id);
    if (!state) return true;

    if (frame_index + 256 < state.highestAcceptedIndex) {
        // Loop / restart detected: reset and accept anything.
        // TELEMETRY: log every loop/restart so we can distinguish "file is
        // looping normally" from "worker lost state" in the console.
        console.debug(`[Worker] Loop/restart reset for ${feed_id}: prev_highest=${state.highestAcceptedIndex}, new=${frame_index}`);
        state.highestAcceptedIndex = frame_index;
        return true;
    }
    if (frame_index <= state.highestAcceptedIndex) {
        // TELEMETRY: silent drops here cause canvas freezes. Log every drop
        // with the (prev, incoming) so the console shows the exact moment
        // backpressure / out-of-order / duplicate frames are filtered.
        // Aggregate via a per-feed drop counter so a noisy reconnect doesn't
        // flood the console -- only log on first drop in a window or when
        // the drop rate crosses a threshold.
        state.dropCount = (state.dropCount || 0) + 1;
        const dropped = state.dropCount;
        const highest = state.highestAcceptedIndex;
        if (dropped === 1 || dropped % 50 === 0) {
            console.debug(`[Worker] DROPPING stale or duplicate frame for ${feed_id}: incoming=${frame_index}, highest=${highest}, total_drops=${dropped}`);
        }
        return false; // Older (or duplicate) frame — drop.
    }
    state.highestAcceptedIndex = frame_index;
    return true;
}

/**
 * The core decode loop for a specific feed.
 * Ensures that frames are processed sequentially and only the latest frame is decoded.
 */
async function processFeedQueue(feed_id) {
    const state = processingState.get(feed_id);
    if (!state || !state.latestPayload) return;

    state.isProcessing = true;

    try {
        while (state.latestPayload) {
            const payload = state.latestPayload;
            state.latestPayload = null;

            const { binaryFrame, frameData, metadata } = payload;
            let jpegBytes = null;

            if (binaryFrame) {
                // binaryFrame is the decoded MessagePack object.
                // Based on backend convention, the image is usually in the 'bg' field.
                jpegBytes = binaryFrame.bg || binaryFrame;
            } else if (frameData) {
                jpegBytes = frameData;
            }

            if (!jpegBytes) continue;

            try {
                let blob;
                if (jpegBytes instanceof ArrayBuffer || jpegBytes instanceof Uint8Array) {
                    blob = new Blob([jpegBytes], { type: 'image/jpeg' });
                } else if (typeof jpegBytes === 'string') {
                    const byteString = atob(jpegBytes);
                    const ab = new ArrayBuffer(byteString.length);
                    const ia = new Uint8Array(ab);
                    for (let i = 0; i < byteString.length; i++) {
                        ia[i] = byteString.charCodeAt(i);
                    }
                    blob = new Blob([ab], { type: 'image/jpeg' });
                }

                if (!blob) continue;

                const bitmap = await createImageBitmap(blob, {
                    imageOrientation: 'none',
                    premultiplyAlpha: 'none',
                    colorSpaceConversion: 'none'
                });

                self.postMessage({
                    feed_id: feed_id,
                    frame: bitmap,
                    frame_index: metadata.frame_index,
                    metrics: metadata.metrics,
                    vehicles: metadata.vehicles,
                    lanes: metadata.lanes,
                    timestamp: metadata.timestamp
                }, [bitmap]);

            } catch (err) {
                console.error(`[Worker] Decode error for feed ${feed_id}:`, err);
                self.postMessage({
                    error: err.message,
                    feed_id: feed_id,
                    frame_index: metadata.frame_index
                });
            }
        }
    } finally {
        state.isProcessing = false;
    }
}

self.onmessage = async function (e) {
    const data = e.data;
    const command = data.command;
    const feed_id = data.feed_id;

    if (command === 'CLEANUP_FEED') {
        processingState.delete(feed_id);
        return;
    }

    // Case 1: Raw Binary Message (from the optimized WebSocketClient)
    if (data.rawBinary) {
        try {
            const decoded = MessagePack.decode(new Uint8Array(data.rawBinary));
            const f_id = decoded.f;
            if (!f_id) return;

            const metadata = {
                frame_index: decoded.i || 0,
                metrics: decoded.m,
                vehicles: decoded.v,
                lanes: decoded.ln,
                timestamp: decoded.ts
            };

            let state = processingState.get(f_id);
            if (!state) {
                state = { isProcessing: false, latestPayload: null, highestAcceptedIndex: -1, coalescedCount: 0, dropCount: 0 };
                processingState.set(f_id, state);
            }

            if (!shouldAccept(f_id, metadata.frame_index)) {
                return;
            }

            // TELEMETRY: detect coalesced (silently overwritten) frames. When a
            // new frame arrives while a previous decode is in flight, we replace
            // latestPayload with the NEW payload and the OLD payload is dropped.
            // This is by-design per the worker docstring ("only the latest one is
            // processed"), but a sustained high coalesce rate means the worker
            // can't keep up with decode and the user sees a stuttering stream.
            // Aggregate via a per-feed counter so we can see exactly when the
            // worker becomes the bottleneck (vs the backend deque, which has its
            // own counter).
            if (state.isProcessing && state.latestPayload) {
                state.coalescedCount = (state.coalescedCount || 0) + 1;
                const c = state.coalescedCount;
                if (c === 1 || c % 25 === 0) {
                    console.debug(`[Worker] Coalesced incoming frame for ${f_id} (decode in flight): coalesced=${c}, incoming_index=${metadata.frame_index}, pending_index=${state.latestPayload.metadata.frame_index}`);
                }
            }

            state.latestPayload = {
                binaryFrame: decoded,
                frameData: null,
                metadata: metadata
            };

            if (!state.isProcessing) {
                processFeedQueue(f_id);
            }
        } catch (err) {
            console.error(`[Worker] Msgpack decode error:`, err);
        }
        return;
    }

    // Case 2: Existing Legacy Message Format
    if (feed_id) {
        const binaryFrame = data.binaryFrame;
        const frameData = data.frameData;

        let metadata = {
            frame_index: 0,
            metrics: null,
            vehicles: [],
            lanes: null,
            timestamp: Date.now()
        };

        if (binaryFrame) {
            metadata = {
                frame_index: binaryFrame.i || 0,
                metrics: binaryFrame.m,
                vehicles: binaryFrame.v,
                lanes: binaryFrame.ln,
                timestamp: binaryFrame.ts
            };
        }

        let state = processingState.get(feed_id);
        if (!state) {
            state = { isProcessing: false, latestPayload: null, highestAcceptedIndex: -1, coalescedCount: 0, dropCount: 0 };
            processingState.set(feed_id, state);
        }

        if (!shouldAccept(feed_id, metadata.frame_index)) {
            return;
        }

        // TELEMETRY: coalesced-frame detection (mirror of the binary path above).
        if (state.isProcessing && state.latestPayload) {
            state.coalescedCount = (state.coalescedCount || 0) + 1;
            const c = state.coalescedCount;
            if (c === 1 || c % 25 === 0) {
                console.debug(`[Worker] Coalesced incoming frame for ${feed_id} (decode in flight): coalesced=${c}, incoming_index=${metadata.frame_index}, pending_index=${state.latestPayload.metadata.frame_index}`);
            }
        }

        state.latestPayload = {
            binaryFrame,
            frameData,
            metadata
        };

        if (!state.isProcessing) {
            processFeedQueue(feed_id);
        }
    }
};
