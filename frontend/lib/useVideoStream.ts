import { useState, useEffect, useRef } from 'react';
import useVideoSocket from './useVideoSocket';
import useMultipartStream from './useMultipartStream';

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

  // Conditionally use the appropriate streaming hook
  const { frameData: wsFrameData, metrics: wsMetrics, isConnected: wsConnected, error: wsError, drawFrame: wsDrawFrame, frameRate } = useVideoSocket(streamId);
  const { image: mpFrameData, error: mpError, isLoading: mpLoading, drawFrame: mpDrawFrame } = useMultipartStream(forceSample ? SAMPLE_VIDEO_URL : `/api/v1/stream/${streamId}`, null); // Assuming multipart stream needs a full URL

  useEffect(() => {
    if (forceSample) {
      setIsLive(false);
      setIsLoading(false);
      return;
    }

    let currentFrameData: Uint8Array | null = null;
    let currentDrawFrame: ((ctx: CanvasRenderingContext2D, frame: Uint8Array) => void) | null = null;
    let currentError: string | null = null;
    let currentIsConnected = false;

    if (streamType === 'websocket') {
      currentFrameData = wsFrameData;
      currentDrawFrame = wsDrawFrame;
      currentError = wsError;
      currentIsConnected = wsConnected;
    } else if (streamType === 'multipart') {
      currentFrameData = mpFrameData;
      currentDrawFrame = mpDrawFrame;
      currentError = mpError;
      currentIsConnected = !mpLoading; // Assuming not loading means connected for multipart
    }

    if (currentIsConnected) {
      setIsLive(true);
      setIsLoading(false);
    } else if (currentError) {
      setError('Failed to connect to video stream');
      setIsLive(false);
      setIsLoading(false);
    }

    const canvas = canvasRef.current;
    if (canvas && currentFrameData && currentDrawFrame) {
      const ctx = canvas.getContext('2d');
      if (ctx) {
        currentDrawFrame(ctx, currentFrameData);
      }
    }
  }, [streamId, forceSample, streamType, wsFrameData, wsDrawFrame, wsConnected, wsError, mpFrameData, mpDrawFrame, mpError, mpLoading]);

  return { videoUrl: null, isLoading, error, isLive, kpis: wsMetrics, canvasRef, frameRate };
};

export default useVideoStream;
