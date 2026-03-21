import React, { useEffect, useRef, useState, useMemo } from 'react';
import * as THREE from 'three';
import LeafletMap from '@/components/map/LeafletMap';
import { WebSocketClient, WebSocketMessageType } from '../lib/websocket/WebSocketClient';
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

  const containerRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.OrthographicCamera | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const instancedMeshRef = useRef<THREE.InstancedMesh | null>(null);
  const dummy = useMemo(() => new THREE.Object3D(), []);

  // WebGL Initialization
  useEffect(() => {
    if (!containerRef.current) return;

    const width = containerRef.current.clientWidth || 800;
    const height = containerRef.current.clientHeight || 600;

    const scene = new THREE.Scene();
    const camera = new THREE.OrthographicCamera(0, width, 0, height, 1, 1000);
    camera.position.z = 10;
    
    const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(window.devicePixelRatio);
    containerRef.current.appendChild(renderer.domElement);

    // Create a simple plane geometry for the markers
    const geometry = new THREE.PlaneGeometry(1, 1);
    const material = new THREE.MeshBasicMaterial({ color: 0x00ff41, transparent: true, opacity: 0.8 });
    
    // Support up to 5000 vehicles in one draw call
    const instancedMesh = new THREE.InstancedMesh(geometry, material, 5000);
    instancedMesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
    scene.add(instancedMesh);

    sceneRef.current = scene;
    cameraRef.current = camera;
    rendererRef.current = renderer;
    instancedMeshRef.current = instancedMesh;

    const handleResize = () => {
      if (!containerRef.current) return;
      const w = containerRef.current.clientWidth;
      const h = containerRef.current.clientHeight;
      renderer.setSize(w, h);
      camera.right = w;
      camera.bottom = h;
      camera.updateProjectionMatrix();
    };

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      renderer.dispose();
      if (containerRef.current) containerRef.current.removeChild(renderer.domElement);
    };
  }, []);

  // Animation Loop (High-Performance WebGL)
  useEffect(() => {
    const animate = () => {
      const now = Date.now();
      const dt = (now - lastTimeRef.current) / 1000;
      lastTimeRef.current = now;

      if (rendererRef.current && sceneRef.current && cameraRef.current && instancedMeshRef.current) {
        const mesh = instancedMeshRef.current;
        
        // Update all instances
        vehicles.forEach((v, i) => {
          if (i >= 5000) return;
          
          // Dead Reckoning: Smooth extrapolation
          const x = v.bbox[0] + (v.vx || 0) * dt;
          const y = v.bbox[1] + (v.vy || 0) * dt;
          const w = v.bbox[2] - v.bbox[0];
          const h = v.bbox[3] - v.bbox[1];

          dummy.position.set(x + w/2, y + h/2, 0);
          dummy.scale.set(w, h, 1);
          dummy.updateMatrix();
          mesh.setMatrixAt(i, dummy.matrix);
        });

        mesh.count = Math.min(vehicles.length, 5000);
        mesh.instanceMatrix.needsUpdate = true;
        
        rendererRef.current.render(sceneRef.current, cameraRef.current);
      }

      requestRef.current = requestAnimationFrame(animate);
    };

    requestRef.current = requestAnimationFrame(animate);
    return () => {
      if (requestRef.current) cancelAnimationFrame(requestRef.current);
    };
  }, [vehicles, dummy]);

  return (
    <section 
      ref={containerRef}
      className="col-span-2 border border-[#00ff41]/30 rounded-md relative h-[600px] overflow-hidden"
    >
      {/* Background Map */}
      <LeafletMap />

      {/* THREE.js overlay will be appended here */}
      
      <div className="absolute top-2 right-2 bg-black/80 border border-[#00ff41]/30 text-[#00ff41] p-2 text-xs font-mono z-[1002]">
        Active Vehicles: {vehicles.length}
      </div>
    </section>
  );
};

export default TrafficMap;