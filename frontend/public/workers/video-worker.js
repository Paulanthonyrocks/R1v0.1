
self.onmessage = async function(e) {
    const { frameData, feed_id, originalData } = e.data;

    try {
        if (!frameData) {
            throw new Error('No frame data provided');
        }

        // 1. Base64 Decode to Uint8Array
        const binaryString = atob(frameData);
        const len = binaryString.length;
        const bytes = new Uint8Array(len);
        for (let i = 0; i < len; i++) {
            bytes[i] = binaryString.charCodeAt(i);
        }

        // 2. Decode JPEG to ImageBitmap (highly efficient, transferable)
        // This offloads the heaviest part of video processing from the main thread.
        let frameToSend;
        let transferables = [];

        if (typeof createImageBitmap !== 'undefined') {
            try {
                const blob = new Blob([bytes], { type: 'image/jpeg' });
                const imageBitmap = await createImageBitmap(blob);
                frameToSend = imageBitmap;
                transferables.push(imageBitmap);
            } catch (err) {
                console.warn('Worker: createImageBitmap failed, falling back to ArrayBuffer', err);
                frameToSend = bytes.buffer;
                transferables.push(bytes.buffer);
            }
        } else {
            frameToSend = bytes.buffer;
            transferables.push(bytes.buffer);
        }

        // Send back to main thread with original metadata
        self.postMessage({ 
            feed_id: feed_id,
            frame: frameToSend,
            frame_index: originalData?.frame_index || 0,
            metrics: originalData?.metrics,
            vehicles: originalData?.vehicles,
            timestamp: originalData?.timestamp
        }, transferables);
    } catch (error) {
        console.error('Worker: Frame decoding failed', error);
        self.postMessage({ error: error.message, feed_id: feed_id });
    }
};
