"use client";

import React, { useEffect, useRef, useState, useMemo } from 'react';
import * as THREE from 'three';
import LeafletMap from '@/components/map/LeafletMap';
import { useWebSocket } from '../lib/websocket/WebSocketProvider';
import { WebSocketMessageType } from '../lib/websocket/WebSocketClient';
import { useVehicleSelection } from '@/lib/context/VehicleSelectionContext';
import { useVehicleTracking } from '../lib/hooks/useVehicleTracking';
import { WebSocketVideoFrame } from '../lib/types/api';
import { Activity, Crosshair, Trash2 } from 'lucide-react';
import { cn } from '@/lib/utils';

const TrafficMap: React.FC = () => {
  const { vehicles, mergeVehicleUpdates } = useVehicleTracking();
  const { selectedGlobalId, setSelectedGlobalId } = useVehicleSelection();
  const client = useWebSocket();
  const [showAll, setShowAll] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const canvasRef = useRef<HTMLCanvasElement>(null);
  
  useEffect(() => {
    if (selectedGlobalId && vehicles) {
      const matching = vehicles.find(v => v.global_vehicle_id === selectedGlobalId);
      if (matching) {
        setSelectedIds(prev => {
          if (prev.has(matching.vehicle_id)) return prev;
          const next = new Set(prev);
          next.add(matching.vehicle_id);
          return next;
        });
      }
    }
  }, [selectedGlobalId, vehicles]);
  const requestRef = useRef<number | null>(null);
  const lastTimeRef = useRef<number>(Date.now());

  useEffect(() => {
    const unsubscribe = client.subscribe(WebSocketMessageType.VIDEO_FRAME, (data: unknown) => {
      const frame = data as WebSocketVideoFrame;
      if (frame && frame.v) {
        mergeVehicleUpdates(frame.v);
      }
    });

    return () => {
      unsubscribe();
    };
  }, [client, mergeVehicleUpdates]);

  const containerRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<THREE.Scene | null>(null);
  const cameraRef = useRef<THREE.OrthographicCamera | null>(null);
  const rendererRef = useRef<THREE.WebGLRenderer | null>(null);
  const instancedMeshRef = useRef<THREE.InstancedMesh | null>(null);
  const dummy = useMemo(() => new THREE.Object3D(), []);

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
    renderer.domElement.style.position = 'absolute';
    renderer.domElement.style.top = '0';
    renderer.domElement.style.left = '0';
    renderer.domElement.style.pointerEvents = 'none';
    containerRef.current.appendChild(renderer.domElement);

    const geometry = new THREE.PlaneGeometry(1, 1);
    const material = new THREE.MeshBasicMaterial({ color: 0xB6FFB0, transparent: true, opacity: 0.8 });
    
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

  useEffect(() => {
    const animate = () => {
      const now = Date.now();
      const dt = (now - lastTimeRef.current) / 1000;
      lastTimeRef.current = now;

      if (rendererRef.current && sceneRef.current && cameraRef.current && instancedMeshRef.current) {
        const mesh = instancedMeshRef.current;
        let drawnCount = 0;
        
        vehicles.forEach((v) => {
          if (drawnCount >= 5000) return;
          
          const isSelected = selectedIds.has(v.vehicle_id) || (v.global_vehicle_id && selectedIds.has(v.global_vehicle_id));
          if (!showAll && !isSelected) return;

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
      const x = (e.clientX - rect.left) / rect.width;
      const y = (e.clientY - rect.top) / rect.height;

      const clickedVehicle = vehicles.find(v => {
        if (!v.bbox || !Array.isArray(v.bbox)) return false;
        const [vx1, vy1, vx2, vy2] = v.bbox;
        return x >= vx1 && x <= vx2 && y >= vy1 && y <= vy2;
      });

      if (clickedVehicle) {
        const gid = clickedVehicle.global_vehicle_id;
        const vid = clickedVehicle.vehicle_id;
        const id = gid || vid;
        
        setSelectedIds(prev => {
          const next = new Set(prev);
          if (next.has(id)) next.delete(id);
          else next.add(id);
          return next;
        });

        if (gid) {
          setSelectedGlobalId(id);
        }
      }
    };

    const el = rendererRef.current.domElement;
    el.addEventListener('click', handleClick);
    return () => el.removeEventListener('click', handleClick);
  }, [vehicles]);

  return (
    <section 
      ref={containerRef}
      className="border border-lcd-green/30 bg-industrial-panel relative h-full w-full overflow-hidden font-lcd"
    >
      {/* Tactical Viewport Frame */}       <div className="absolute top-0 left-0 w-2 h-2 border-t-2 border-l-2 border-lcd-green z-[1003]" />       <div className="absolute top-0 right-0 w-2 h-2 border-t-2 border-r-2 border-lcd-green z-[1003]" />       <div className="absolute bottom-0 left-0 w-2 h-2 border-b-2 border-l-2 border-lcd-green z-[1003]" />       <div className="absolute bottom-0 right-0 w-2 h-2 border-b-2 border-r-2 border-lcd-green z-[1003]" />        {/* Viewport Overlays */}       <div className="absolute inset-0 pointer-events-none z-[1001] opacity-20">         <div className="absolute top-1/2 left-0 w-full h-px bg-lcd-green" />         <div className="absolute top-0 left-1/2 w-px h-full bg-lcd-green" />       </div>       <div className="absolute inset-0 pointer-events-none z-[1001] opacity-10 bg-[repeating-linear-gradient(0deg,transparent,transparent_2px,var(--lcd-green)_2px,var(--lcd-green)_4px)]" />        {/* Background Map */}       <LeafletMap />        {/* THREE.js overlay will be appended here */}              <div className="absolute top-4 right-4 flex flex-col gap-3 z-[1002] font-lcd">         <div className="bg-industrial-panel/90 border border-lcd-green/30 text-lcd-green p-3 text-xs uppercase tracking-widest shadow-lg backdrop-blur-sm">           <div className="flex items-center gap-2 mb-1 opacity-50 text-[9px]">             <Activity className="h-3 w-3" />             <span>Telemetry_Stream</span>           </div>           <div className="flex justify-between items-center gap-8">             <span>Active Units:</span>             <span className="font-bold">{vehicles.length}</span>           </div>         </div>          <div className="bg-industrial-panel/90 border border-lcd-green/30 text-lcd-green p-3 text-xs uppercase tracking-widest shadow-lg backdrop-blur-sm">           <label className="cursor-pointer flex items-center gap-3 group">             <div className="relative w-4 h-4 border border-lcd-green flex items-center justify-center overflow-hidden">                <input                   type="checkbox"                   checked={showAll}                   onChange={e => setShowAll(e.target.checked)}                  className="absolute opacity-0 w-full h-full cursor-pointer z-10"                />                <div className={cn("absolute w-full h-full bg-lcd-green transition-all duration-200", showAll ? "top-0" : "top-full")} />             </div>             <span className="group-hover:text-white transition-colors">Show All Units</span>           </label>         </div>          {selectedIds.size > 0 && (           <button              onClick={() => setSelectedIds(new Set())}             className="bg-red-500/10 border border-red-500/50 text-red-500 p-2 text-[10px] font-bold uppercase tracking-tighter hover:bg-red-500 hover:text-white transition-all flex items-center justify-center gap-2"           >             <Trash2 size={12} />             Clear Selection ({selectedIds.size})           </button>         )}       </div>              {/* Viewport Crosshair */}       <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 pointer-events-none z-[1002] opacity-30 text-lcd-green">         <Crosshair size={40} strokeWidth={1} />       </div>     </section>   ); };  export default TrafficMap;