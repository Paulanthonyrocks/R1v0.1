
const canvases = new Map();
const contexts = new Map();
self.onmessage = async function (e) {
    const { command, feed_id: command_feed_id } = e.data;

    // Handle Commands
    if (command === 'CLEANUP_FEED') {
        const feed_id = command_feed_id;
        const canvas = canvases.get(feed_id);
        if (canvas) {
            // Note: OffscreenCanvas doesn't have a close() method, but we can let it be GC'd
            // by removing all references to it.
            canvases.delete(feed_id);
            console.log(`[Worker] Cleaned up resources for feed: ${feed_id}`);
        }
        const ctx = contexts.get(feed_id);
        if (ctx) {
            contexts.delete(feed_id);
        }
        return;
    }

    const { binaryFrame, frameData, feed_id, originalData } = e.data;
...
            const { background, rois, frame, frame_index, metrics, vehicles, timestamp } = binaryFrame;

            metadata = { frame_index, metrics, vehicles, timestamp };

            if (background && rois) {
                // Adaptive ROI Reconstruction
                const bgBlob = new Blob([background], { type: 'image/jpeg' });
                const bgBitmap = await createImageBitmap(bgBlob);

                // Initialize OffscreenCanvas for this specific feed if needed
                let canvas = canvases.get(feed_id);
                let ctx = contexts.get(feed_id);

                if (!canvas || canvas.width !== bgBitmap.width || canvas.height !== bgBitmap.height) {
                    canvas = new OffscreenCanvas(bgBitmap.width, bgBitmap.height);
                    ctx = canvas.getContext('2d', { alpha: false }); // Disable alpha for perf
                    canvases.set(feed_id, canvas);
                    contexts.set(feed_id, ctx);
                }

                // Draw background
                ctx.drawImage(bgBitmap, 0, 0);
                bgBitmap.close();

                // Draw ROI patches
                for (const roi of rois) {
                    const roiBlob = new Blob([roi.b], { type: 'image/jpeg' });
                    const roiBitmap = await createImageBitmap(roiBlob);
                    ctx.drawImage(roiBitmap, roi.x, roi.y, roi.w, roi.h);
                    roiBitmap.close();
                }

                frameToSend = canvas.transferToImageBitmap();
                transferables.push(frameToSend);
            } else if (frame) {
                // Raw binary frame (Binary Fallback)
                const blob = new Blob([frame], { type: 'image/jpeg' });
                frameToSend = await createImageBitmap(blob);
                transferables.push(frameToSend);
            }
        }
        // 2. Handle Base64 Data (Legacy Protocol - Fallback)
        else if (frameData) {
            const binaryString = atob(frameData);
            const bytes = new Uint8Array(binaryString.length);
            for (let i = 0; i < binaryString.length; i++) {
                bytes[i] = binaryString.charCodeAt(i);
            }
            const blob = new Blob([bytes], { type: 'image/jpeg' });
            frameToSend = await createImageBitmap(blob);
            transferables.push(frameToSend);
        }

        if (frameToSend) {
            self.postMessage({
                feed_id: feed_id,
                frame: frameToSend,
                frame_index: metadata.frame_index || 0,
                metrics: metadata.metrics,
                vehicles: metadata.vehicles,
                timestamp: metadata.timestamp
            }, transferables);
        }
    } catch (error) {
        console.error('Worker: Frame processing failed', error);
        self.postMessage({ error: error.message, feed_id: feed_id });
    }
};
