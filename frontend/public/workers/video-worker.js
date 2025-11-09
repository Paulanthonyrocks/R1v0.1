
self.onmessage = function(e) {
    const { frameData } = e.data;

    try {
        // Assuming frameData is base64 encoded string
        const decodedData = atob(frameData);
        const byteNumbers = new Array(decodedData.length);
        for (let i = 0; i < decodedData.length; i++) {
            byteNumbers[i] = decodedData.charCodeAt(i);
        }
        const byteArray = new Uint8Array(byteNumbers);

        self.postMessage({ frame: byteArray.buffer }, [byteArray.buffer]);
    } catch (error) {
        console.error('Worker: Frame decoding failed', error);
        // Optionally, post an error message back to the main thread
        self.postMessage({ error: error.message });
    }
};
