import React, { useEffect, useRef, useState, useMemo } from 'react';
import * as THREE from 'three';
import LeafletMap from '@/components/map/LeafletMap';
import { WebSocketClient, WebSocketMessageType } from '../lib/websocket/WebSocketClient';
import { useVehicleTracking } from '../lib/hooks/useVehicleTracking';
import { WebSocketVideoFrame } from '../lib/types/api';

const TrafficMap: React.FC = () => {
  const { vehicles, mergeVehicleUpdates } = useVehicleTracking();
  const [wsClient, setWsClient] = useState<WebSocketClient | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
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
        let drawnCount = 0;
        
        // Update all instances
        vehicles.forEach((v) => {
          if (drawnCount >= 5000) return;
          
          const isSelected = selectedIds.has(v.vehicle_id) || (v.global_vehicle_id && selectedIds.has(v.global_vehicle_id));
          if (!showAll && !isSelected) return;

          // Dead Reckoning: Smooth extrapolation
          // Assumption: bbox is in pixels or needs scaling. 
          // If bbox is normalized [0, 1], scale by renderer size
          const canvasW = rendererRef.current!.domElement.width / window.devicePixelRatio;
          const canvasH = rendererRef.current!.domElement.height / window.devicePixelRatio;

          const x = (v.bbox[0] + (v.vx || 0) * dt) * canvasW;
          const y = (v.bbox[1] + (v.vy || 0) * dt) * canvasH;
          const w = (v.bbox[2] - v.bbox[0]) * canvasW;
          const h = (v.bbox[3] - v.bbox[1]) * canvasH;

          dummy.position.set(x + w/2, y + h/2, 0);
          dummy.scale.set(w, h, 1);
          dummy.updateMatrix();
          mesh.setMatrixAt(drawnCount, dummy.matrix);
          drawnCount++;
        });

        mesh.count = drawnCount;
        mesh.instanceMatrix.needsUpdate = true;
        
        rendererRef.current.render(sceneRef.current, cameraRef.current);
      }

      requestRef.current = requestAnimationFrame(animate);
    };

    requestRef.current = requestAnimationFrame(animate);
    return () => {
      if (requestRef.current) cancelAnimationFrame(requestRef.current);
    };
  }, [vehicles, dummy, showAll, selectedIds]);

  useEffect(() => {
    if (!containerRef.current || !rendererRef.current) return;
    
    const handleClick = (e: MouseEvent) => {
      if (!containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      // Normalize click coordinates to [0, 1] to match normalized bbox
      const x = (e.clientX - rect.left) / rect.width;
      const y = (e.clientY - rect.top) / rect.height;

      console.debug(`[TrafficMap] Clicked at normalized: (${x.toFixed(3)}, ${y.toFixed(3)})`);

      const clickedVehicle = vehicles.find(v => {
        if (!v.bbox || !Array.isArray(v.bbox)) return false;
        const [vx1, vy1, vx2, vy2] = v.bbox;
        return x >= vx1 && x <= vx2 && y >= vy1 && y <= vy2;
      });

      if (clickedVehicle) {
        const id = clickedVehicle.global_vehicle_id || clickedVehicle.vehicle_id;
        console.debug(`[TrafficMap] Vehicle found: ${id}`);
        setSelectedIds(prev => {
          const next = new Set(prev);
          if (next.has(id)) next.delete(id);
          else next.add(id);
          return next;
        });
      }
    };

    const el = rendererRef.current.domElement;
    el.addEventListener('click', handleClick);
    return () => el.removeEventListener('click', handleClick);
  }, [vehicles]);

  return (
    <section 
      ref={containerRef}
      className="col-span-2 border border-[#00ff41]/30 rounded-md relative h-[600px] overflow-hidden"
    >
      {/* Background Map */}
      <LeafletMap />

      {/* THREE.js overlay will be appended here */}
      
      <div className="absolute top-2 right-2 flex flex-col gap-2 z-[1002]">
        <div className="bg-black/80 border border-[#00ff41]/30 text-[#00ff41] p-2 text-xs font-mono">
          Active Vehicles: {vehicles.length}
        </div>
        <div className="bg-black/80 border border-[#00ff41]/30 text-[#00ff41] p-2 text-xs font-mono flex items-center gap-2">
          <label className="cursor-pointer flex items-center gap-2">
            <input 
              type="checkbox" 
              checked={showAll} 
              onChange={e => setShowAll(e.target.checked)}
              className="accent-[#00ff41]"
            />
            SHOW ALL
          </label>
        </div>
        {selectedIds.size > 0 && (
          <button 
            onClick={() => setSelectedIds(new Set())}
            className="bg-black/80 border border-red-500/50 text-red-500 p-1 text-[10px] font-mono hover:bg-red-500/20"
          >
            CLEAR SELECTION ({selectedIds.size})
          </button>
        )}
      </div>
    </section>
  );
};

export default TrafficMap;
