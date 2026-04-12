"use client";

import React, { useState, useRef } from 'react';
import dynamic from 'next/dynamic';
import 'leaflet/dist/leaflet.css';
import { Activity, Layers, Video, Map as MapIcon, Zap, AlertTriangle, X, ZoomIn, ZoomOut, Navigation, Maximize, Check } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useVehicleTracking } from '@/lib/hooks/useVehicleTracking';
import DashboardHeader from '@/components/dashboard/DashboardHeader';

// Dynamically import TrafficMap to avoid SSR issues with THREE.js and Leaflet
const DynamicTrafficMap = dynamic(() => import('@/components/TrafficMap'), {
  ssr: false,
  loading: () => (
    <div className="fixed inset-0 bg-industrial-bg text-lcd-green font-lcd flex items-center justify-center z-50">
      <div className="flex flex-col items-center gap-4">
        <div className="w-12 h-12 border-4 border-lcd-green border-t-transparent rounded-full animate-spin" />
        <div className="animate-pulse text-xl uppercase tracking-[0.3em]">Initializing Neural Grid...</div>
      </div>
    </div>
  )
});

const LiveMapPage: React.FC = () => {
  const { vehicles } = useVehicleTracking();
  const mapRef = useRef<any>(null);
  const [activeLayer, setActiveLayer] = useState<'satellite' | 'vector' | 'thermal'>('satellite');
  const [showLayerControl, setShowLayerControl] = useState(false);
  const [showAllUnits, setShowAllUnits] = useState(false);
  const [activeLayers, setActiveLayers] = useState({
    feeds: true,
    congestion: true,
    incidents: true,
    roads: true,
    signals: true
  });

  return (
    <div className="flex flex-col h-screen bg-industrial-bg overflow-hidden">
      <DashboardHeader />
      <div className="relative flex-1 bg-industrial-bg overflow-hidden">
        <style jsx global>{`
          .leaflet-marker-icon {
            background-image: url('https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png') !important;
          }
          .leaflet-marker-shadow {
            background-image: url('https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png') !important;
          }
        `}</style>
        {/* TOP HUD: Title and Global Status */}
        <div className="absolute top-0 left-0 right-0 z-20 pointer-events-none px-6 py-4 flex items-center justify-between">
           <div className="flex flex-col">
              <h1 className="text-2xl font-bold uppercase tracking-[0.4em] text-lcd-green drop-shadow-[0_0_10px_rgba(182,255,176,0.5)] font-lcd">
                Surveillance Grid
              </h1>
              <span className="text-[10px] text-lcd-green/50 uppercase tracking-widest font-mono">
                Sector: North-01 // Status: Active // Sync: 100%
              </span>
           </div>
           <div className="flex items-center gap-4">
              <div className="bg-industrial-panel/80 border border-lcd-green/30 px-3 py-1 flex items-center gap-3 text-lcd-green text-[10px] uppercase tracking-tighter backdrop-blur-sm shadow-lg">
                 <div className="flex items-center gap-2">
                   <Activity className="h-3 w-3 animate-pulse" />
                   <span>Real-time Telemetry Active</span>
                 </div>
                 <div className="w-px h-3 bg-lcd-green/30" />
                 <div className="flex items-center gap-1 font-bold">
                   <span>Units:</span>
                   <span>{vehicles?.length || 0}</span>
                 </div>
              </div>
              
              {/* Layer Control Toggle */}
              <button 
                onClick={() => setShowLayerControl(!showLayerControl)}
                className="bg-industrial-panel/80 border border-lcd-green/30 p-2 text-lcd-green hover:bg-lcd-green hover:text-industrial-bg transition-all backdrop-blur-sm shadow-lg pointer-events-auto"
              >
                <Layers size={16} />
              </button>
           </div>
        </div>

        {/* Layer Control Menu */}
        {showLayerControl && (
          <div className="absolute top-20 right-6 z-30 bg-industrial-panel/90 border border-lcd-green/30 p-4 w-56 font-mono text-xs text-lcd-green flex flex-col gap-3 shadow-2xl backdrop-blur-md pointer-events-auto">
            <div className="flex justify-between items-center mb-1 pb-1 border-b border-lcd-green/20">
              <span className="font-bold uppercase tracking-widest">Intelligence Layers</span>
              <X size={14} className="cursor-pointer hover:text-white" onClick={() => setShowLayerControl(false)} />
            </div>
            {[
              { id: 'feeds', icon: Video, label: 'Surveillance Nodes' },
              { id: 'congestion', icon: Activity, label: 'Congestion Heat' },
              { id: 'roads', icon: MapIcon, label: 'Network Topology' },
              { id: 'signals', icon: Zap, label: 'Signal Controllers' },
              { id: 'incidents', icon: AlertTriangle, label: 'Incident Events' }
            ].map((layer) => (
              <label key={layer.id} className="flex items-center gap-3 cursor-pointer group">
                <input 
                  type="checkbox" 
                  checked={(activeLayers as any)[layer.id]} 
                  onChange={() => setActiveLayers(prev => ({ ...prev, [layer.id]: !(prev as any)[layer.id] }))} 
                  className="hidden" 
                />
                <div className={cn(
                  "w-3 h-3 border flex items-center justify-center transition-all duration-200", 
                  (activeLayers as any)[layer.id] 
                    ? "bg-lcd-green border-lcd-green" 
                    : "border-lcd-green/30 bg-black/20"
                )}>
                  {(activeLayers as any)[layer.id] && <Check size={8} className="text-industrial-bg stroke-[3px]" />}
                </div>
                <layer.icon size={14} className="opacity-60 group-hover:opacity-100 transition-opacity" />
                <span className={cn("group-hover:translate-x-1 transition-transform", !(activeLayers as any)[layer.id] && "opacity-40")}>{layer.label}</span>
              </label>
            ))}
            <div className="mt-2 pt-2 border-t border-lcd-green/20">
              <label className="flex items-center gap-3 cursor-pointer group">
                <input 
                  type="checkbox" 
                  checked={showAllUnits} 
                  onChange={e => setShowAllUnits(e.target.checked)} 
                  className="hidden" 
                />
                <div className={cn(
                  "w-3 h-3 border flex items-center justify-center transition-all duration-200", 
                  showAllUnits 
                    ? "bg-lcd-green border-lcd-green" 
                    : "border-lcd-green/30 bg-black/20"
                )}>
                  {showAllUnits && <Check size={8} className="text-industrial-bg stroke-[3px]" />}
                </div>
                <span className={cn("group-hover:translate-x-1 transition-transform", !showAllUnits && "opacity-40")}>Show All Units</span>
              </label>
            </div>
            <div className="mt-4 pt-2 border-t border-lcd-green/20">
              <div className="flex items-center gap-2 mb-2 uppercase font-bold opacity-60 text-[10px] tracking-widest">
                <span>Map Legend</span>
              </div>
              <div className="grid grid-cols-2 gap-x-2 gap-y-2">
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 rounded-full bg-[#ff3e3e] shadow-[0_0_5px_#ff3e3e]"></div>
                  <span className="text-[10px]">CRITICAL</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-2 h-2 rounded-full bg-[#00ff41] shadow-[0_0_5px_#00ff41]"></div>
                  <span className="text-[10px]">OPTIMAL</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-3 h-3 border border-[#ff3e3e] rotate-45 flex items-center justify-center text-[7px] font-bold text-[#ff3e3e]">!</div>
                  <span className="text-[10px]">INCIDENT</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="flex flex-col gap-[1px]">
                    <div className="w-1.5 h-1.5 bg-[#00ff41] rounded-full"></div>
                    <div className="w-1.5 h-1.5 bg-[#ff3e3e] rounded-full"></div>
                  </div>
                  <span className="text-[10px]">SIGNAL</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-3 h-3 border border-[#00ff41] rounded-sm flex items-center justify-center text-[7px] font-bold text-[#00ff41]">
                    <div className="w-1.5 h-1.5 bg-[#00ff41] rounded-sm"></div>
                  </div>
                  <span className="text-[10px]">FEED</span>
                </div>
              </div>
            </div>
          </div>
        )}

        {/* RIGHT HUD: Map Tools */}
        <div className="absolute right-6 top-1/2 -translate-y-1/2 z-20 flex flex-col gap-2 pointer-events-auto">
          <button 
            onClick={() => mapRef.current?.zoomIn()}
            className="p-2 bg-industrial-panel/80 border border-lcd-green/30 text-lcd-green hover:bg-lcd-green hover:text-industrial-bg transition-all shadow-lg backdrop-blur-sm" 
            title="Zoom In"
          >
            <ZoomIn size={18} />
          </button>
          <button 
            onClick={() => mapRef.current?.zoomOut()}
            className="p-2 bg-industrial-panel/80 border border-lcd-green/30 text-lcd-green hover:bg-lcd-green hover:text-industrial-bg transition-all shadow-lg backdrop-blur-sm" 
            title="Zoom Out"
          >
            <ZoomIn size={18} />
          </button>
          <button 
            onClick={() => mapRef.current?.center()}
            className="p-2 bg-industrial-panel/80 border border-lcd-green/30 text-lcd-green hover:bg-lcd-green hover:text-industrial-bg transition-all shadow-lg backdrop-blur-sm" 
            title="Center View"
          >
            <Navigation size={18} />
          </button>
          <button 
            onClick={() => {
              if (document.fullscreenElement) {
                document.exitFullscreen();
              } else {
                document.documentElement.requestFullscreen();
              }
            }}
            className="p-2 bg-industrial-panel/80 border border-lcd-green/30 text-lcd-green hover:bg-lcd-green hover:text-industrial-bg transition-all shadow-lg backdrop-blur-sm" 
            title="Full Screen"
          >
            <Maximize size={18} />
          </button>
        </div>

        <DynamicTrafficMap ref={mapRef} activeLayer={activeLayer} activeLayers={activeLayers} showAllUnits={showAllUnits} />
      </div>
    </div>
  );
};

export default LiveMapPage;