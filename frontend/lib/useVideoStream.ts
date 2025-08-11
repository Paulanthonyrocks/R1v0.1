import { useState, useEffect, useRef } from 'react';
import useVideoSocket from './useVideoSocket';
import useMultipartStream from './useMultipartStream';

import useAuth from '@/lib/hook/useAuth';
const SAMPLE_VIDEO_URL = '/sample-video.mp4';

interface UseVideoStreamOptions {
  streamId: string;
  forceSample?: boolean;
  streamType?: 'websocket' | 'multipart'; // New prop to specify stream type
}

const useVideoStream = ({ streamId, forceSample = false, streamType = 'websocket' }: UseVideoStreamOptions) => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isLive, setIsLive] = useState(false);
  const { token } = useAuth(); // Get token from useAuth

  // Conditionally use the appropriate streaming hook
  const { frameData: wsFrameData, metrics: wsMetrics, isConnected: wsConnected, error: wsError, drawFrame: wsDrawFrame, frameRate } = useVideoSocket(streamId, token);
  const { rawData: mpRawFrameData, error: mpError, isLoading: mpLoading, drawFrame: mpDrawFrame } = useMultipartStream(forceSample ? SAMPLE_VIDEO_URL : `/api/v1/stream/${streamId}`, token); // Pass the token, get rawData

  useEffect(() => {
    if (forceSample) {
      setIsLive(false);
      setIsLoading(false);
      return;
    }

    let currentRawFrameData: Uint8Array | null = null;
    let currentDrawFrame: ((ctx: CanvasRenderingContext2D, frame: Uint8Array) => void) | null = null;
    let currentError: string | null = null;
    let currentIsConnected = false;

    // Select the correct frame data and draw function based on stream type
    if (streamType === 'websocket') {
      currentRawFrameData = wsFrameData;
      currentDrawFrame = wsDrawFrame;
      currentError = wsError;
    }

    // Determine connection status based on stream type
    currentIsConnected = streamType === 'websocket' ? wsConnected : !mpLoading;
    if (currentIsConnected) {
      setIsLive(true);
    } else if (currentError && !currentIsConnected) { // Only set error if not connected and there's an error
      setError('Failed to connect to video stream');
      setIsLoading(false);
    }

    const canvas = canvasRef.current;
    // Ensure we have a canvas, data to draw, and a drawing function
    if (canvas && currentRawFrameData && currentDrawFrame) { // Added null check for currentDrawFrame
      const ctx = canvas.getContext('2d');
      if (ctx) {
        // Pass the raw data to the drawing function
        currentDrawFrame(ctx, currentRawFrameData);
      }
    } else if (canvas) {
      // Optionally clear the canvas if data or draw function is missing but canvas exists
      const ctx = canvas.getContext('2d');
      if (ctx) {
          ctx.clearRect(0, 0, canvas.width, canvas.height);
      }
    }
  }, [streamId, forceSample, streamType, wsFrameData, wsDrawFrame, wsConnected, wsError, mpRawFrameData, mpDrawFrame, mpError, mpLoading]);
  return { videoUrl: null, isLoading, error, isLive, kpis: wsMetrics, canvasRef, frameRate };
};

export default useVideoStream;
