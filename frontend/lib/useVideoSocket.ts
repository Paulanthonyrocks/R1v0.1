import { useState, useEffect, useCallback, useRef } from 'react';
import { WebSocketClient, WebSocketMessageType } from './websocket/WebSocketClient';

interface KpiData {
  timestamp: string;
  vehicle_count: number;
  motion_level: number;
  frame_number: number;
}

const useVideoSocket = (streamId: string, token: string | null) => {
  const [frameData, setFrameData] = useState<Uint8Array | null>(null);
  const [metrics, setMetrics] = useState<any | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [frameRate, setFrameRate] = useState<number>(0);
  const lastFrameTimeRef = useRef<number>(0);

  const handleFrame = useCallback((data: { frame: string }) => {
    // Assuming data.frame is a base64 encoded string
    const byteCharacters = atob(data.frame);
    const byteNumbers = new Array(byteCharacters.length);
    for (let i = 0; i < byteCharacters.length; i++) {
      byteNumbers[i] = byteCharacters.charCodeAt(i);
    }
    setFrameData(new Uint8Array(byteNumbers));

    const now = performance.now();
    if (lastFrameTimeRef.current !== 0) {
      const frameTime = now - lastFrameTimeRef.current;
      setFrameRate(1000 / frameTime);
    }
    lastFrameTimeRef.current = now;
  }, []);

  const drawFrame = useCallback((ctx: CanvasRenderingContext2D, frame: Uint8Array) => {
    const img = new Image();
    const blob = new Blob([frame], { type: 'image/jpeg' });
    img.onload = () => {
      ctx.drawImage(img, 0, 0, ctx.canvas.width, ctx.canvas.height);
      URL.revokeObjectURL(img.src); // Clean up the object URL
    };
    img.src = URL.createObjectURL(blob);
  }, []);

  const handleMetrics = useCallback((data: any) => {
    setMetrics(data);
  }, []);

  useEffect(() => {
    if (!token || !streamId) return;

    const wsClient = new WebSocketClient(`ws://localhost:8000/api/v1/video/ws/${streamId}?token=${token}`);

    const connect = async () => {
      try {
        await wsClient.connect(token);
        setIsConnected(true);
      } catch (err) {
        setError('Failed to connect to video WebSocket: ' + (err instanceof Error ? err.message : 'Unknown error'));
      }
    };

    connect();

    wsClient.subscribe(WebSocketMessageType.VIDEO_FRAME, handleFrame);
    wsClient.subscribe(WebSocketMessageType.FEED_METRICS, handleMetrics);

    return () => {
      wsClient.unsubscribe(WebSocketMessageType.VIDEO_FRAME, handleFrame);
      wsClient.unsubscribe(WebSocketMessageType.FEED_METRICS, handleMetrics);
      wsClient.disconnect();
    };
  }, [streamId, handleFrame, handleMetrics, token]);

  return { frameData, metrics, isConnected, error, drawFrame, frameRate };
};

export default useVideoSocket;
