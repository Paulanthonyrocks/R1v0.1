// frontend/lib/useMultipartStream.ts
import { useState, useEffect, useRef, useCallback } from 'react';

interface MultipartStreamData {
  image: string | null; // Data URL for the image
  error: string | null;
  isLoading: boolean;
}

const useMultipartStream = (url: string | null, token: string | null): MultipartStreamData & { drawFrame: (ctx: CanvasRenderingContext2D, frame: Uint8Array) => void } => {
  const [frameData, setFrameData] = useState<Uint8Array | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);

  const abortControllerRef = useRef<AbortController | null>(null);

  const drawFrame = useCallback((ctx: CanvasRenderingContext2D, frame: Uint8Array) => {
    const img = new Image();
    const blob = new Blob([frame], { type: 'image/jpeg' });
    img.onload = () => {
      ctx.drawImage(img, 0, 0, ctx.canvas.width, ctx.canvas.height);
      URL.revokeObjectURL(img.src); // Clean up the object URL
    };
    img.src = URL.createObjectURL(blob);
  }, []);

  useEffect(() => {
    if (!url) {
      setFrameData(null);
      setError(null);
      setIsLoading(false);
      return;
    }

    // Prepend API base URL if url is relative
    let fetchUrl = url;
    if (url.startsWith('/')) {
      const apiBase = process.env.NEXT_PUBLIC_API_URL || '';
      fetchUrl = apiBase.replace(/.+$/, '') + url;
    }

    setIsLoading(true);
    setError(null);
    abortControllerRef.current = new AbortController();
    const signal = abortControllerRef.current.signal;

    const readStream = async () => {
      try {
        const headers: HeadersInit = {};
        if (token) {
          headers['Authorization'] = `Bearer ${token}`;
        }

        const response = await fetch(fetchUrl, { headers, signal });

        if (!response.ok || !response.body) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const contentType = response.headers.get('Content-Type');
        if (!contentType || !contentType.startsWith('multipart/x-mixed-replace')) {
          throw new Error(`Unexpected Content-Type: ${contentType}`);
        }

        const boundaryMatch = contentType.match(/boundary=(.+)$/);
        if (!boundaryMatch || !boundaryMatch[1]) {
          throw new Error('Could not find boundary in Content-Type header');
        }
        const boundary = '--' + boundaryMatch[1];
        const boundaryBytes = new TextEncoder().encode(`\r\n${boundary}`);
        const chunkReader = response.body.getReader();

        let buffer = new Uint8Array();

        while (true) {
          const { done, value } = await chunkReader.read();

          if (done) {
            break;
          }

          // Append the new chunk to the buffer
          const newBuffer = new Uint8Array(buffer.length + value.length);
          newBuffer.set(buffer);
          newBuffer.set(value, buffer.length);
          buffer = newBuffer;

          let boundaryIndex = indexOf(buffer, boundaryBytes);
          while (boundaryIndex !== -1) {
            let fullBoundaryFound = true;
            for (let i = 1; i < boundaryBytes.length; i++) {
              if (buffer[boundaryIndex + i] !== boundaryBytes[i]) {
                fullBoundaryFound = false;
                break;
              }
            }

            if (fullBoundaryFound) {
              const part = buffer.slice(0, boundaryIndex);
              buffer = buffer.slice(boundaryIndex + boundaryBytes.length); // Keep the rest of the buffer

              // Process the part
              const headersEnd = findSequence(part, new TextEncoder().encode('\r\n\r\n'));

              if (headersEnd > -1) {
                const headersText = new TextDecoder().decode(part.slice(0, headersEnd));

                if (headersText.includes('Content-Type: image/jpeg')) {
                   const imagePart = part.slice(headersEnd + 4);
                   setFrameData(imagePart);
                }
              }

              // Check for the next boundary in the remaining buffer
              boundaryIndex = indexOf(buffer, boundaryBytes);

            } else {
              // If it looked like a start but wasn't the full boundary,
              // or if we're inside a part, continue reading.
              break;
            }
          }
        }

        setIsLoading(false);

      } catch (err: unknown) {
        if (signal.aborted) {
          console.log('Stream fetch aborted');
        } else {
          const errorMsg = err instanceof Error ? err.message : String(err);
          console.error("Stream fetch error:", err);
          setError(errorMsg || "Failed to fetch stream");
          setIsLoading(false);
        }
      }
    };

    readStream();

    return () => {
      console.log("Aborting stream fetch");
      abortControllerRef.current?.abort();
    };

  }, [url, token]);

  return { image: frameData, error, isLoading, drawFrame };
};

// Helper function to find byte sequence
function indexOf(buffer: Uint8Array, sequence: Uint8Array): number {
    if (sequence.length === 0) return 0;
    if (sequence.length > buffer.length) return -1;

    for (let i = 0; i <= buffer.length - sequence.length; i++) {
        let found = true;
        for (let j = 0; j < sequence.length; j++) {
            if (buffer[i + j] !== sequence[j]) {
                found = false;
                break;
            }
        }
        if (found) {
            return i;
        }
    }
    return -1;
}

// Optimized helper to find a sequence within a Uint8Array
function findSequence(buffer: Uint8Array, sequence: Uint8Array): number {
  const len = buffer.length;
  const seqLen = sequence.length;
  if (seqLen === 0) return 0;
  if (seqLen > len) return -1;

  for (let i = 0; i <= len - seqLen; i++) {
    let match = true;
    for (let j = 0; j < seqLen; j++) {
      if (buffer[i + j] !== sequence[j]) {
        match = false;
        break;
      }
    }
    if (match) {
      return i;
    }
  }
  return -1;
}

export default useMultipartStream;