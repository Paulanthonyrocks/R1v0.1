/**
 * Video Worker — decodes inbound JPEG frames into ImageBitmaps.
 *
 * Design goals:
 *  1. Coalesce frames: If multiple frames arrive before the previous one is decoded,
 *     only the latest one is processed.
 *  2. Strict feed isolation: Each feed has its own processing state to prevent "juggling".
 *  3. Memory efficiency: Use ImageBitmap and transfer ownership to the main thread.
 *  4. Priority: Use binary msgpack path as primary, base64 as fallback.
 */

importScripts('https://cdn.jsdelivr.net/npm/@msgpack/msgpack@3.0.0-beta.2/dist/msgpack.min.js');

// State tracking per feed
const processingState = new Map(); // feed_id → { isProcessing: boolean, latestPayload: null | object }

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
                jpegBytes = binaryFrame.bg;
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
                timestamp: decoded.ts
            };

            if (!processingState.has(f_id)) {
                processingState.set(f_id, { isProcessing: false, latestPayload: null });
            }
            
            const state = processingState.get(f_id);
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
            timestamp: Date.now()
        };

        if (binaryFrame) {
            metadata = {
                frame_index: binaryFrame.i || 0,
                metrics: binaryFrame.m,
                vehicles: binaryFrame.v,
                timestamp: binaryFrame.ts
            };
        }

        if (!processingState.has(feed_id)) {
            processingState.set(feed_id, { isProcessing: false, latestPayload: null });
        }
        
        const state = processingState.get(feed_id);
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
