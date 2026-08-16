import { useRef, useCallback, useEffect } from 'react';
import { VideoFrameMessage } from '../types';

interface DecoderOptions {
    onFrame: (frame: {
        image: ImageBitmap | HTMLImageElement;
        index: number;
        metrics: any;
        vehicles: any[];
        lanes: any;
        timestamp: number;
    }) => void;
    onError?: (error: Error) => void;
}

export const useVideoDecoder = (options: DecoderOptions) => {
    const optionsRef = useRef(options);

    useEffect(() => {
        optionsRef.current = options;
    }, [options]);

    const decode = useCallback(async (data: VideoFrameMessage) => {
        try {
            let decodedImage: ImageBitmap | HTMLImageElement | null = null;

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
                optionsRef.current.onFrame({
                    image: decodedImage,
                    index: data.frame_index || 0,
                    metrics: data.metrics || null,
                    vehicles: data.vehicles || [],
                    lanes: data.lanes || null,
                    timestamp: typeof data.timestamp === 'number' ? data.timestamp : Date.now()
                });
            }
        } catch (error: any) {
            optionsRef.current.onError?.(error);
        }
    }, []);

    return { decode };
};
