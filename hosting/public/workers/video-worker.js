
let canvas = null;
let ctx = null;

self.onmessage = async function (e) {
    const { binaryFrame, frameData, feed_id, originalData } = e.data;

    try {
        let frameToSend;
        let transferables = [];
        let metadata = originalData || {};

        // 1. Handle Binary Data (New Protocol)
        if (binaryFrame) {
            const { background, rois, frame, frame_index, metrics, vehicles, timestamp } = binaryFrame;

            metadata = { frame_index, metrics, vehicles, timestamp };

            if (background && rois) {
                // Adaptive ROI Reconstruction
                const bgBlob = new Blob([background], { type: 'image/jpeg' });
                const bgBitmap = await createImageBitmap(bgBlob);

                // Initialize OffscreenCanvas if needed
                if (!canvas || canvas.width !== bgBitmap.width || canvas.height !== bgBitmap.height) {
                    canvas = new OffscreenCanvas(bgBitmap.width, bgBitmap.height);
                    ctx = canvas.getContext('2d');
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
