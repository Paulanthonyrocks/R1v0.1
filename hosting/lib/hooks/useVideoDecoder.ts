import { useRef, useCallback } from 'react';
import { VideoFrameMessage } from '../types';

interface DecoderOptions {
    onFrame: (frame: {
        image: ImageBitmap;
        index: number;
        metrics: any;
        vehicles: any[];
        timestamp: number;
    }) => void;
    onError?: (error: Error) => void;
}

export const useVideoDecoder = (options: DecoderOptions) => {
    const isProcessingRef = useRef<boolean>(false);

    const decode = useCallback(async (data: VideoFrameMessage) => {
        if (isProcessingRef.current) return;
        isProcessingRef.current = true;

        try {
            let decodedImage: ImageBitmap | null = null;

            if (data.frame instanceof ImageBitmap) {
                decodedImage = data.frame;
            } else if (data.frame instanceof ArrayBuffer || typeof data.frame === 'string') {
                try {
                    let byteArray: Uint8Array;
                    if (typeof data.frame === 'string') {
                        const byteString = atob(data.frame);
                        byteArray = new Uint8Array(byteString.length);
                        for (let i = 0; i < byteString.length; i++) {
                            byteArray[i] = byteString.charCodeAt(i);
                        }
                    } else {
                        byteArray = new Uint8Array(data.frame);
                    }

                    const blob = new Blob([byteArray.buffer as BlobPart], { type: 'image/jpeg' });
                    if ('createImageBitmap' in window) {
                        decodedImage = await createImageBitmap(blob);
                    } else {
                        decodedImage = await new Promise((resolve, reject) => {
                            const img = new Image();
                            img.onload = () => resolve(img);
                            img.onerror = reject;
                            img.src = URL.createObjectURL(blob);
                        });
                    }
                } catch (err) {
                    throw new Error('Main thread decoding fallback failed');
                }
            }

            if (decodedImage) {
                options.onFrame({
                    image: decodedImage,
                    index: data.frame_index || 0,
                    metrics: data.metrics || null,
                    vehicles: data.vehicles || [],
                    timestamp: data.timestamp || Date.now()
                });
            }
        } catch (error: any) {
            options.onError?.(error);
        } finally {
            isProcessingRef.current = false;
        }
    }, [options]);

    return { decode };
};
