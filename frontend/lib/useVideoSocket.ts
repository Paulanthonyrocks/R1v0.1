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

      if (metrics && metrics.vehicles) {
        ctx.strokeStyle = 'red';
        ctx.lineWidth = 2;
        ctx.font = '12px Arial';
        ctx.fillStyle = 'white';

        metrics.vehicles.forEach(v => {
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
      return; // Do not connect if essential info is missing.
    }

    const wsUrl = new URL(`/api/v1/video/ws/${streamId}`, VIDEO_WS_BASE_URL).toString();
    console.log('Constructed Video WebSocket URL:', wsUrl);

    const client = new WebSocketClient(wsUrl);
    wsClientRef.current = client;

    // Subscribe to video frame and metrics updates
    client.subscribe(WebSocketMessageType.VIDEO_FRAME, handleFrame);
    client.subscribe(WebSocketMessageType.METRICS_UPDATE, handleMetrics);

    // Handle connection status changes
    client.onStatusChange((status, message) => {
      console.log(`Video WebSocket status for ${streamId}: ${status} - ${message || ''}`);
      setIsConnected(status === 'connected');
      if (status === 'error' || status === 'disconnected') {
        setError(message || 'Video stream connection error.');
      } else {
        setError(null);
      }
    });

    // Handle errors
    client.onError((type, message) => {
      console.error(`Video WebSocket Error (${type}) for ${streamId}:`, message);
      setError(message);
    });

    // Connect the WebSocket
    console.log(`useVideoSocket: Connecting to ${streamId}...`);
    client.connect(token);

    // Return a cleanup function that will run ONLY when the component unmounts.
    // This is critical for preventing duplicate connections in StrictMode.
    return () => {
      console.log(`useVideoSocket: Disconnecting from ${streamId}.`);
      client.disconnect();
      wsClientRef.current = null;
    };
  // The dependency array ensures this effect re-runs only if the stream or user token changes.
  }, [streamId, token]);

  return { frameData, metrics, isConnected, error, drawFrame, frameRate };
};

export default useVideoSocket;
