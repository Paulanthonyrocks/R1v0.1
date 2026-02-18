import React, { useEffect, useRef, useState } from 'react';
import ThreeGrid from '@/components/CesiumGlobe';
import { WebSocketClient, WebSocketMessageType, WebSocketMessage } from '../lib/websocket/WebSocketClient';
import { useVehicleTracking } from '../lib/hooks/useVehicleTracking';
import { WebSocketVideoFrame } from '../lib/types/api';

const TrafficMap: React.FC = () => {
  const { vehicles, mergeVehicleUpdates } = useVehicleTracking();
  const [wsClient, setWsClient] = useState<WebSocketClient | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const requestRef = useRef<number | null>(null);
  const lastTimeRef = useRef<number>(Date.now());

  // Connect to WebSocket
  useEffect(() => {
    const client = new WebSocketClient(process.env.NEXT_PUBLIC_WS_URL || 'ws://localhost:8000/ws');
    client.activate();
    client.connect(null); // Assuming auto-auth or no auth for dev

    const unsubscribe = client.subscribe(WebSocketMessageType.VIDEO_FRAME, (data: unknown) => {
      const frame = data as WebSocketVideoFrame;
      if (frame && frame.v) {
        mergeVehicleUpdates(frame.v);
      }
    });

    setWsClient(client);

    return () => {
      unsubscribe();
      client.destroy();
    };
  }, [mergeVehicleUpdates]);

  // Animation Loop for Dead Reckoning
  useEffect(() => {
    const animate = () => {
      const now = Date.now();
      const dt = (now - lastTimeRef.current) / 1000; // seconds
      lastTimeRef.current = now;

      const canvas = canvasRef.current;
      const ctx = canvas?.getContext('2d');

      if (canvas && ctx) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);

        // Visualize Vehicles
        vehicles.forEach(v => {
          // Dead Reckoning: Extrapolate position locally based on velocity
          // timestamp of data vs now could be used for more precise sync
          // For simple smoothing:
          let x = v.bbox[0];
          let y = v.bbox[1];

          // Simple simulation of movement (in pixels)
          // In real app, you'd update local state or use a rigid body
          // Here we just draw what we have, but if we wanted to extrapolate:
          // x += (v.vx || 0) * dt; 
          // y += (v.vy || 0) * dt;

          const w = v.bbox[2] - v.bbox[0];
          const h = v.bbox[3] - v.bbox[1];

          // Draw BBox
          ctx.strokeStyle = '#00ff00';
          ctx.lineWidth = 2;
          ctx.strokeRect(x, y, w, h);

          // Draw Info
          ctx.fillStyle = '#00ff00';
          ctx.font = '12px Arial';
          ctx.fillText(`${v.class_name} ${v.vehicle_id.substring(0, 4)}`, x, y - 5);

          // Visualize ReID Confidence (Gallery Size)
          if (v.gallery_size !== undefined) {
            ctx.fillStyle = '#ffff00';
            ctx.fillText(`Views: ${v.gallery_size} Vel: ${v.vx?.toFixed(1)},${v.vy?.toFixed(1)}`, x, y + h + 15);
          }
        });
      }

      requestRef.current = requestAnimationFrame(animate);
    };

    requestRef.current = requestAnimationFrame(animate);
    return () => {
      if (requestRef.current) cancelAnimationFrame(requestRef.current);
    };
  }, [vehicles]); // Re-bind when vehicle list changes (or use Ref for vehicles to avoid re-binding)

  return (
    <section className="col-span-2 border border-gray-300 rounded-md relative h-[600px]">
      {/* Background 3D/Map */}
      <ThreeGrid />

      {/* Overlay for Vehicle Visualization */}
      <canvas
        ref={canvasRef}
        className="absolute top-0 left-0 w-full h-full pointer-events-none"
        width={800} // Should be dynamic based on container
        height={600}
      />
      <div className="absolute top-2 right-2 bg-black/50 text-white p-2 text-xs">
        Active Vehicles: {vehicles.length}
      </div>
    </section>
  );
};

export default TrafficMap;