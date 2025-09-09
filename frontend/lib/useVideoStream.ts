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

const useVideoStream = ({ streamId, forceSample = false, streamType = 'websocket', frameData, kpis, showOverlays, showBoundingBoxes, showVehicleDetails }: UseVideoStreamOptions): UseVideoStreamReturn => {
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

  const drawFrame = useCallback((ctx: CanvasRenderingContext2D, frame: Uint8Array) => {
    const img = new Image();
    const blob = new Blob([frame.slice().buffer], { type: 'image/jpeg' });
    img.onload = () => {
      ctx.drawImage(img, 0, 0, ctx.canvas.width, ctx.canvas.height);
      URL.revokeObjectURL(img.src);
    };
    img.src = URL.createObjectURL(blob);
  }, []);

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
            const frame = new Uint8Array(frameData);
            drawFrame(ctx, frame);

            // Drawing logic for overlays (bounding boxes, vehicle details)
            if (showOverlays && kpis && kpis.kpis && kpis.kpis.vehicles) {
              console.log("Drawing overlays. Bounding Boxes:", showBoundingBoxes, "Vehicle Details:", showVehicleDetails);
              // Draw in canvas coordinate space; the frame was drawn to canvas size already
              const scaleX = 1;
              const scaleY = 1;

              ctx.font = '10px monospace';
              ctx.fillStyle = '#00FF00'; // Green color for text

              // Fixed: Use the correct structure for vehicle data
              kpis.kpis.vehicles.forEach((detection: { x1: number; y1: number; x2: number; y2: number; id: string; speed: number; }) => {
                const { x1, y1, x2, y2 } = detection;
                
                if (showBoundingBoxes) {
                  ctx.strokeStyle = '#FF0000'; // Red color for boxes
                  ctx.lineWidth = 2;
                  ctx.strokeRect(x1 * scaleX, y1 * scaleY, (x2 - x1) * scaleX, (y2 - y1) * scaleY);
                }

                if (showVehicleDetails) {
                  ctx.fillText(`ID: ${detection.id}`, x1 * scaleX, y1 * scaleY - 10);
                  ctx.fillText(`SPD: ${detection.speed} KM/H`, x1 * scaleX, y1 * scaleY - 25);
                }
              });
            }
          }
        }
      } else {
        console.log("useVideoStream: frameData is null/undefined. Setting isLoading=true, isLive=false.");
        setIsLive(false);
        setIsLoading(true);
      }
    } else { // multipart
      if (!mpLoading && mpRawFrameData) {
        setIsLive(true);
        setIsLoading(false);
        setError(null);
        const canvas = canvasRef.current;
        if (canvas) {
          const ctx = canvas.getContext('2d');
          if (ctx) {
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
  }, [streamId, forceSample, streamType, frameData, mpRawFrameData, mpLoading, mpError, drawFrame, mpDrawFrame, showOverlays, showBoundingBoxes, showVehicleDetails, kpis]);

  return { videoUrl: null, isLoading, error, isLive, canvasRef, frameRate, kpis };
};

export default useVideoStream;