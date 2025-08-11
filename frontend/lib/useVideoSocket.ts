import { useState, useEffect, useCallback, useRef } from 'react';
import { WebSocketMessageType } from './websocket/WebSocketClient';
import { SurveillanceFeedMessage } from './types';

const useVideoSocket = (streamId: string, token: string | null) => {
  const [frameData, setFrameData] = useState<Uint8Array | null>(null);
  const [metrics, setMetrics] = useState<SurveillanceFeedMessage | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [frameRate, setFrameRate] = useState<number>(0);
  const lastFrameTimeRef = useRef<number>(0);
  const wsRef = useRef<WebSocket | null>(null);

  const VIDEO_WS_BASE_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000';

  const handleFrame = useCallback((data: { frame: string }) => {
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
      URL.revokeObjectURL(img.src);
    };
    img.src = URL.createObjectURL(blob);
  }, []);

  const handleMetrics = useCallback((data: SurveillanceFeedMessage) => {
    setMetrics(data);
  }, []);

  useEffect(() => {
    if (!token || !streamId) return;

    const wsUrl = new URL(`/api/v1/video/ws/${streamId}`, VIDEO_WS_BASE_URL);
    wsUrl.searchParams.set('token', token);

    const ws = new WebSocket(wsUrl.toString());
    wsRef.current = ws;

    ws.onopen = () => {
      console.log(`Video WebSocket opened for stream: ${streamId}`);
      setIsConnected(true);
      setError(null);
    };

    ws.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === WebSocketMessageType.VIDEO_FRAME) {
          handleFrame(message.data);
        } else if (message.type === WebSocketMessageType.METRICS_UPDATE) {
          handleMetrics(message.data);
        }
      } catch (e) {
        console.error('Error processing video WebSocket message:', e);
      }
    };

    ws.onerror = (event) => {
      console.error('Video WebSocket error:', event);
      setError('Video stream connection error.');
    };

    ws.onclose = (event) => {
      console.log(`Video WebSocket closed for stream: ${streamId}. Code: ${event.code}`);
      setIsConnected(false);
    };

    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [streamId, token, handleFrame, handleMetrics, VIDEO_WS_BASE_URL]);

  return { frameData, metrics, isConnected, error, drawFrame, frameRate };
};

export default useVideoSocket;
