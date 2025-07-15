// frontend/lib/useMultipartStream.ts
import { useState, useEffect, useRef } from 'react';

interface MetricsData {
  [key: string]: unknown;
}

interface MultipartStreamData {
  image: string | null; // Data URL for the image
  metrics: MetricsData | null; // Parsed JSON data
  error: string | null;
  isLoading: boolean;
}

const useMultipartStream = (url: string | null, token: string | null): MultipartStreamData => {
  const [imageData, setImageData] = useState<string | null>(null);
  const [metricsData, setMetricsData] = useState<MetricsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(false);

  const abortControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!url) {
      setImageData(null);
      setMetricsData(null);
      setError(null);
      setIsLoading(false);
      return;
    }

    // Prepend API base URL if url is relative
    let fetchUrl = url;
    if (url.startsWith('/')) {
      const apiBase = process.env.NEXT_PUBLIC_API_URL || '';
      fetchUrl = apiBase.replace(/\/$/, '') + url;
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
        let imagePart: Uint8Array | null = null;
        let metricsPart: Uint8Array | null = null;

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
              const partText = new TextDecoder().decode(part);
              const headersEnd = partText.indexOf('\r\n\r\n');

              if (headersEnd > -1) {
                const headers = partText.substring(0, headersEnd);
                // const body = part.slice(headersEnd + 4); // Removed unused variable

                if (headers.includes('Content-Type: image/jpeg')) {
                   // Find the actual start of the image data (after headers and potential newline)
                    const imageStart = partText.substring(0, headersEnd).length + 4;
                    imagePart = part.slice(imageStart);
                } else if (headers.includes('Content-Type: application/json')) {
                    const jsonStart = partText.substring(0, headersEnd).length + 4;
                    metricsPart = part.slice(jsonStart);
                }
              }

              // If we have both parts, process and update state
              if (imagePart && metricsPart) {
                  const imageUrl = URL.createObjectURL(new Blob([imagePart], { type: 'image/jpeg' }));
                  setImageData(imageUrl);

                  try {
                      const jsonText = new TextDecoder().decode(metricsPart);
                      setMetricsData(JSON.parse(jsonText) as MetricsData);
                  } catch (jsonError: unknown) {
                      console.error("Error parsing metrics JSON:", jsonError);
                      setMetricsData({ error: "Failed to parse metrics" });
                  }

                  // Clean up old blob URL to prevent memory leaks
                   if (imageData && typeof imageData === 'string' && imageData.startsWith('blob:')) {
                      URL.revokeObjectURL(imageData);
                   }

                  // Reset for the next frame + metrics pair
                  imagePart = null;
                  metricsPart = null;
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
       // Clean up the last blob URL on unmount
       if (imageData && typeof imageData === 'string' && imageData.startsWith('blob:')) {
            URL.revokeObjectURL(imageData);
       }
    };

  }, [url, token, imageData]); // Added imageData to dependency array

  return { image: imageData, metrics: metricsData, error, isLoading };
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


export default useMultipartStream;