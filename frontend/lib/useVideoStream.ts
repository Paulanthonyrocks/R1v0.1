import { useState, useEffect, useRef, useCallback } from 'react';
import useMultipartStream from './useMultipartStream';
import useAuth from '@/lib/hook/useAuth';
import { SurveillanceFeedMessage } from '@/lib/types';

const SAMPLE_VIDEO_URL = '/sample-video.mp4';

interface UseVideoStreamOptions {
  streamId: string;
  forceSample?: boolean;
  streamType?: 'websocket' | 'multipart';
  frameData?: ArrayBuffer | null;
  kpis?: SurveillanceFeedMessage;
  showOverlays?: boolean;
  showBoundingBoxes?: boolean;
  showVehicleDetails?: boolean;
isFullScreen?: boolean;
}

interface UseVideoStreamReturn {
  videoUrl: string | null;
  isLoading: boolean;
  error: string | null;
  isLive: boolean;
  canvasRef: React.RefObject<HTMLCanvasElement | null>;
  frameRate: number;
  kpis: SurveillanceFeedMessage | null | undefined;
}

const useVideoStream = ({
  streamId,
  forceSample = false,
  streamType = 'websocket',
  frameData,
  kpis,
  showOverlays,
  showBoundingBoxes,
  showVehicleDetails,
  isFullScreen,
}: UseVideoStreamOptions): UseVideoStreamReturn => {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isLive, setIsLive] = useState(false);
  const { token } = useAuth();
  const lastFrameTimeRef = useRef<number>(0);
  const [frameRate, setFrameRate] = useState<number>(0);

  const { rawData: mpRawFrameData, error: mpError, isLoading: mpLoading, drawFrame: mpDrawFrame } = useMultipartStream(
    (streamType === 'multipart' || forceSample) ? (forceSample ? SAMPLE_VIDEO_URL : `/api/v1/stream/${streamId}`) : null,
    token
  );

  const drawFrame = useCallback((ctx: CanvasRenderingContext2D, frameBuffer: ArrayBuffer) => {
    const img = new Image();
    const blob = new Blob([frameBuffer], { type: 'image/jpeg' });
    const url = URL.createObjectURL(blob);
    img.onload = () => {
      if (ctx.canvas.offsetWidth !== ctx.canvas.width) {
        ctx.canvas.width = ctx.canvas.offsetWidth;
      }
      if (ctx.canvas.offsetHeight !== ctx.canvas.height) {
        ctx.canvas.height = ctx.canvas.offsetHeight;
      }
      ctx.drawImage(img, 0, 0, ctx.canvas.width, ctx.canvas.height);
      URL.revokeObjectURL(url);
    };
    img.src = url;
  }, []); // frameData is no longer needed here as it's passed directly to useEffect

  useEffect(() => {
    if (forceSample) {
      setIsLive(false);
      setIsLoading(false);
      return;
    }

    if (streamType === 'websocket') {
      if (frameData) {
        setIsLive(true);
        setIsLoading(false);
        setError(null);

        const now = performance.now();
        if (lastFrameTimeRef.current !== 0) {
          const frameTime = now - lastFrameTimeRef.current;
          setFrameRate(1000 / frameTime);
        }
        lastFrameTimeRef.current = now;

        const canvas = canvasRef.current;
        if (canvas) {
          const ctx = canvas.getContext('2d');
          if (ctx) {
            if (isFullScreen) {
                canvas.width = window.innerWidth;
                canvas.height = window.innerHeight;
            } else {
                canvas.width = canvas.offsetWidth;
                canvas.height = canvas.offsetHeight;
            }
            
            drawFrame(ctx, frameData);

            if (showOverlays && kpis && kpis.kpis && kpis.kpis.vehicles) {
              const originalFrameWidth = 640;
              const originalFrameHeight = 480;
              const scaleX = canvas.width / originalFrameWidth;
              const scaleY = canvas.height / originalFrameHeight;

              ctx.font = `${10 * scaleX}px monospace`;
              ctx.fillStyle = '#00FF00';
              ctx.strokeStyle = '#FF0000';
              ctx.lineWidth = 2 * scaleX;

              kpis.kpis.vehicles.forEach((detection: { x1: number; y1: number; x2: number; y2: number; id: string; speed: number; }) => {
                const { x1, y1, x2, y2 } = detection;
                
                if (showBoundingBoxes) {
                  ctx.strokeRect(x1 * scaleX, y1 * scaleY, (x2 - x1) * scaleX, (y2 - y1) * scaleY);
                }

                if (showVehicleDetails) {
                  ctx.fillText(`ID: ${detection.id}`, x1 * scaleX, y1 * scaleY - (10 * scaleY));
                  ctx.fillText(`SPD: ${detection.speed.toFixed(1)} KM/H`, x1 * scaleX, y1 * scaleY - (25 * scaleY));
                }
              });
            }
          }
        }
      } else {
        setIsLive(false);
        setIsLoading(true);
      }
    } else {
      if (!mpLoading && mpRawFrameData) {
        setIsLive(true);
        setIsLoading(false);
        setError(null);
        const canvas = canvasRef.current;
        if (canvas) {
          const ctx = canvas.getContext('2d');
          if (ctx) {
            if (isFullScreen) {
                canvas.width = window.innerWidth;
                canvas.height = window.innerHeight;
            } else {
                canvas.width = canvas.offsetWidth;
                canvas.height = canvas.offsetHeight;
            }
            mpDrawFrame(ctx, mpRawFrameData);
          }
        }
      } else if (mpError) {
        setError('Failed to connect to video stream');
        setIsLoading(false);
        setIsLive(false);
      } else {
        setIsLoading(true);
        setIsLive(false);
      }
    }
  }, [streamId, forceSample, streamType, frameData, mpRawFrameData, mpLoading, mpError, drawFrame, mpDrawFrame, showOverlays, showBoundingBoxes, showVehicleDetails, kpis, isFullScreen]);

  return { videoUrl: null, isLoading, error, isLive, canvasRef, frameRate, kpis };
};

export default useVideoStream;
