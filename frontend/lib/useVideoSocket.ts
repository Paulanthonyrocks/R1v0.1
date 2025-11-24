
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
  const wsClientRef = useRef<WebSocketClient | null>(null);

  const getWsUrl = (path: string) => {
    const baseUrl = (process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000').replace(/\/$/, '');
    return `${baseUrl}${path}`;
  }

  const handleFrame = useCallback((data: { frame: ArrayBuffer | string }) => {
    let byteArray: Uint8Array;
    if (typeof data.frame === 'string') {
        // Handle Base64 string
        const byteString = atob(data.frame);
        const byteNumbers = new Array(byteString.length);
        for (let i = 0; i < byteString.length; i++) {
            byteNumbers[i] = byteString.charCodeAt(i);
        }
        byteArray = new Uint8Array(byteNumbers);
    } else {
        // Handle ArrayBuffer
        byteArray = new Uint8Array(data.frame);
    }
    setFrameData(byteArray);

    const now = performance.now();
    if (lastFrameTimeRef.current > 0) {
        const frameTime = now - lastFrameTimeRef.current;
        setFrameRate(1000 / frameTime);
    }
    lastFrameTimeRef.current = now;
  }, []);

  const drawFrame = useCallback((ctx: CanvasRenderingContext2D, frame: Uint8Array) => {
    const blob = new Blob([frame], { type: 'image/jpeg' });
    const url = URL.createObjectURL(blob);
    const img = new Image();
    img.onload = () => {
        ctx.drawImage(img, 0, 0, ctx.canvas.width, ctx.canvas.height);
        URL.revokeObjectURL(url);

        if (metrics && metrics.kpis?.vehicles) {
            ctx.strokeStyle = 'red';
            ctx.lineWidth = 2;
            ctx.font = '12px Arial';
            ctx.fillStyle = 'white';

            metrics.kpis.vehicles.forEach(v => {
                ctx.strokeRect(v.x1, v.y1, v.x2 - v.x1, v.y2 - v.y1);
                
                const text = `ID: ${v.id} | Speed: ${v.speed.toFixed(1)} km/h`;
                const textWidth = ctx.measureText(text).width;
                ctx.fillStyle = 'rgba(0, 0, 0, 0.6)';
                ctx.fillRect(v.x1, v.y1 - 18, textWidth + 8, 18);

                ctx.fillStyle = 'white';
                ctx.fillText(text, v.x1 + 4, v.y1 - 5);
            });
        }
    };
    img.src = url;
  }, [metrics]);

  const handleMetrics = useCallback((data: SurveillanceFeedMessage) => {
    setMetrics(data);
  }, []);

  useEffect(() => {
    if (!token || !streamId) {
      return;
    }

    const wsUrl = getWsUrl(`/api/v1/video-ws/${streamId}`);

    const connect = () => {
      console.log(`useVideoSocket: Connecting to ${streamId}...`);
      // Pass requiresClientId as false
      const client = new WebSocketClient(wsUrl, false);
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
        }
      });

      client.onError((type, message) => {
        console.error(`Video WebSocket Error (${type}) for ${streamId}:`, message);
        setError(message);
      });

      client.connect(token).catch(err => {
        console.error(`useVideoSocket: Failed to connect to ${streamId}:`, err);
        setError(err.message || 'Failed to connect to video stream.');
      });
    };

    connect();

    return () => {
      console.log(`useVideoSocket: Disconnecting from ${streamId}.`);
      if (wsClientRef.current) {
        wsClientRef.current.disconnect();
        wsClientRef.current = null;
      }
    };
  }, [streamId, token, handleFrame, handleMetrics]);

  return { frameData, metrics, isConnected, error, drawFrame, frameRate };
};

export default useVideoSocket;
