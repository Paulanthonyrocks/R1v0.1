import { useState, useEffect, useCallback, useRef } from 'react';
import { WebSocketClient, WebSocketMessageType } from './websocket/WebSocketClient';
import { SurveillanceFeedMessage } from './types';

const useVideoSocket = (streamId: string, token: string | null) => {
  const [frameData, setFrameData] = useState<Uint8Array | null>(null);
  const [metrics, setMetrics] = useState<SurveillanceFeedMessage | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [frameRate, setFrameRate] = useState<number>(0);
  const lastFrameTimeRef = useRef<number>(0);
  const wsClientRef = useRef<WebSocketClient | null>(null); // Use wsClientRef for WebSocketClient

  const VIDEO_WS_BASE_URL = process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000';

  const handleFrame = useCallback((data: ArrayBuffer) => {
    setFrameData(new Uint8Array(data));

    const now = performance.now();
    if (lastFrameTimeRef.current !== 0) {
      const frameTime = now - lastFrameTimeRef.current;
      setFrameRate(1000 / frameTime);
    }
    lastFrameTimeRef.current = now;
  }, []);

  const drawFrame = useCallback((ctx: CanvasRenderingContext2D, frame: Uint8Array) => {
    const img = new Image();
    const blob = new Blob([frame.slice().buffer], { type: 'image/jpeg' });
    img.onload = () => {
      ctx.drawImage(img, 0, 0, ctx.canvas.width, ctx.canvas.height);
      URL.revokeObjectURL(img.src);

      if (metrics && metrics.kpis && metrics.kpis.vehicles) {
        ctx.strokeStyle = 'red';
        ctx.lineWidth = 2;
        ctx.font = '12px Arial';
        ctx.fillStyle = 'white';

        metrics.kpis.vehicles.forEach(v => {
          // Draw bounding box
          ctx.strokeRect(v.x1, v.y1, v.x2 - v.x1, v.y2 - v.y1);
          
          // Draw text background
          const text = `ID: ${v.id} | Speed: ${v.speed.toFixed(1)} km/h`;
          const textWidth = ctx.measureText(text).width;
          ctx.fillStyle = 'rgba(0, 0, 0, 0.6)';
          ctx.fillRect(v.x1, v.y1 - 18, textWidth + 8, 18);

          // Draw text
          ctx.fillStyle = 'white';
          ctx.fillText(text, v.x1 + 4, v.y1 - 5);
        });
      }
    };
    img.src = URL.createObjectURL(blob);
  }, [metrics]);

  const handleMetrics = useCallback((data: SurveillanceFeedMessage) => {
    setMetrics(data);
  }, []);

  useEffect(() => {
    if (!token || !streamId) {
      return;
    }

    let reconnectTimer: NodeJS.Timeout;
    const wsUrl = new URL(`/video/ws/${streamId}`, VIDEO_WS_BASE_URL).toString();

    const connect = () => {
      console.log(`useVideoSocket: Connecting to ${streamId}...`);
      const client = new WebSocketClient(wsUrl);
      wsClientRef.current = client;

      client.subscribe(WebSocketMessageType.VIDEO_FRAME, handleFrame);
      client.subscribe(WebSocketMessageType.METRICS_UPDATE, handleMetrics);

      client.onStatusChange((status, message) => {
        console.log(`Video WebSocket status for ${streamId}: ${status} - ${message || ''}`);
        setIsConnected(status === 'connected');
        if (status === 'connected') {
          setError(null);
        } else if (status === 'error' || status === 'disconnected') {
          setError(message || 'Video stream connection error.');
          // Clear any existing timer and set a new one to reconnect
          clearTimeout(reconnectTimer);
          reconnectTimer = setTimeout(connect, 5000); // Reconnect after 5 seconds
        }
      });

      client.onError((type, message) => {
        console.error(`Video WebSocket Error (${type}) for ${streamId}:`, message);
        setError(message);
      });

      client.connect(token);
    };

    connect();

    return () => {
      console.log(`useVideoSocket: Disconnecting from ${streamId}.`);
      clearTimeout(reconnectTimer);
      if (wsClientRef.current) {
        wsClientRef.current.disconnect();
        wsClientRef.current = null;
      }
    };
  }, [streamId, token, VIDEO_WS_BASE_URL, handleFrame, handleMetrics]);

  return { frameData, metrics, isConnected, error, drawFrame, frameRate };
};

export default useVideoSocket;
