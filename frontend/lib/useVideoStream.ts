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
  
  // Always call hooks at the top level
  const { rawData: mpRawFrameData, error: mpError, isLoading: mpLoading, drawFrame: mpDrawFrame } = useMultipartStream(
    (streamType === 'multipart' || forceSample) ? (forceSample ? SAMPLE_VIDEO_URL : `/api/v1/stream/${streamId}`) : null,
    token
  );

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

    // Select the correct data source based on stream type
    if (streamType === 'websocket') {
      currentRawFrameData = wsFrameData;
      currentDrawFrame = wsDrawFrame;
      currentError = wsError;
      currentIsConnected = wsConnected;
    } else { // 'multipart'
      currentRawFrameData = mpRawFrameData;
      currentDrawFrame = mpDrawFrame;
      currentError = mpError;
      currentIsConnected = !mpLoading;
    }

    // Update state based on the selected source
    if (currentIsConnected) {
      setIsLive(true);
      setIsLoading(false);
      setError(null);
    } else {
      setIsLive(false);
      // Only set loading to true if we are not in an error state
      if (currentError) {
        setError('Failed to connect to video stream');
        setIsLoading(false);
      } else {
        setIsLoading(true);
      }
    }

    const canvas = canvasRef.current;
    if (canvas && currentRawFrameData && currentDrawFrame) {
      const ctx = canvas.getContext('2d');
      if (ctx) {
        currentDrawFrame(ctx, currentRawFrameData);
      }    } else if (canvas) {
      const ctx = canvas.getContext('2d');
      if (ctx) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
      }
    }
  }, [streamId, forceSample, streamType, wsFrameData, wsDrawFrame, wsConnected, wsError, mpRawFrameData, mpDrawFrame, mpError, mpLoading]);
  return { videoUrl: null, isLoading, error, isLive, kpis: wsMetrics, canvasRef, frameRate };
};

export default useVideoStream;
