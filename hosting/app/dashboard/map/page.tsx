"use client";

import React, { useState } from 'react';
import dynamic from 'next/dynamic';
import AuthGuard from "@/components/auth/AuthGuard";
import { UserRole } from "@/lib/auth/roles";
import DashboardShell from '@/components/dashboard/DashboardShell';
import TrafficMap from '@/components/TrafficMap';
import { Settings, Layers, Eye, ZoomIn, ZoomOut, Navigation, Maximize, Activity, ShieldAlert, ChevronLeft, ChevronRight } from 'lucide-react';
import { cn } from '@/lib/utils';

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
  const [activeLayer, setActiveLayer] = useState<'satellite' | 'vector' | 'thermal'>('satellite');
  const [isLeftCollapsed, setIsLeftCollapsed] = useState(true);
  const [isRightCollapsed, setIsRightCollapsed] = useState(true);

  return (
    <AuthGuard requiredRole={UserRole.VIEWER}>
      <DashboardShell className="p-0 max-w-none h-screen overflow-hidden">
        <div className="relative flex-1 w-full h-full bg-industrial-bg overflow-hidden">
          
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
                <div className="bg-industrial-panel/80 border border-lcd-green/30 px-3 py-1 flex items-center gap-2 text-lcd-green text-[10px] uppercase tracking-tighter backdrop-blur-sm shadow-lg">
                   <Activity className="h-3 w-3 animate-pulse" />
                   <span>Real-time Telemetry Active</span>
                </div>
             </div>
          </div>

          {/* LEFT HUD: Vehicle Inspector */}
          <div className="absolute left-0 top-0 bottom-0 z-20 w-80 pointer-events-none">
             <button 
                onClick={() => setIsLeftCollapsed(!isLeftCollapsed)}
                className={cn(
                  "absolute right-0 top-1/2 -translate-y-1/2 w-6 h-12 bg-lcd-green text-industrial-bg border border-lcd-green z-30 flex items-center justify-center cursor-pointer hover:bg-white transition-colors pointer-events-auto",
                  isLeftCollapsed && "left-0"
                )}
             >
               {isLeftCollapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
             </button>
             
             <div className={cn(
               "absolute inset-0 bg-industrial-panel/80 border border-lcd-green/30 backdrop-blur-md p-4 flex flex-col h-full shadow-2xl transition-transform duration-300 ease-in-out pointer-events-auto",
               isLeftCollapsed ? "-translate-x-full" : "translate-x-0"
             )}>
                {/* Tactical Corner Accents */}
                <div className="absolute top-0 left-0 w-1 h-1 border-t border-l border-lcd-green" />
                <div className="absolute top-0 right-0 w-1 h-1 border-t border-r border-lcd-green" />
                <div className="absolute bottom-0 left-0 w-1 h-1 border-b border-l border-lcd-green" />
                <div className="absolute bottom-0 right-0 w-1 h-1 border-b border-r border-lcd-green" />

                <div className="flex items-center gap-2 mb-4 text-lcd-green uppercase tracking-widest font-bold text-xs">
                   <Eye className="h-4 w-4" />
                   <span>Unit Inspector</span>
                </div>
                
                <div className="flex-1 overflow-y-auto custom-scrollbar space-y-2 opacity-70">
                   <div className="text-[10px] text-lcd-green/40 uppercase mb-2">Select a vehicle on the map to view detailed telemetry...</div>
                   <div className="p-2 border border-lcd-green/10 bg-black/20 text-[10px] font-mono text-lcd-green/60 italic">
                      Waiting for target lock...
                   </div>
                </div>

                <div className="mt-auto pt-4 border-t border-lcd-green/20">
                   <div className="flex items-center justify-between text-[10px] text-lcd-green/50 uppercase mb-2">
                      <span>System Load</span>
                      <span>12%</span>
                   </div>
                   <div className="w-full h-1 bg-lcd-green/10 overflow-hidden">
                      <div className="h-full bg-lcd-green w-[12%] animate-pulse" />
                   </div>
                </div>
             </div>
          </div>

          {/* RIGHT HUD: Map Controls */}
          <div className="absolute right-0 top-0 bottom-0 z-20 w-64 pointer-events-none">
             <button 
                onClick={() => setIsRightCollapsed(!isRightCollapsed)}
                className={cn(
                  "absolute left-0 top-1/2 -translate-y-1/2 w-6 h-12 bg-lcd-green text-industrial-bg border border-lcd-green z-30 flex items-center justify-center cursor-pointer hover:bg-white transition-colors pointer-events-auto",
                  isRightCollapsed && "right-0"
                )}
             >
               {isRightCollapsed ? <ChevronLeft size={14} /> : <ChevronRight size={14} />}
             </button>

             <div className={cn(
               "absolute inset-0 bg-industrial-panel/80 border border-lcd-green/30 backdrop-blur-md p-4 flex flex-col gap-6 shadow-2xl transition-transform duration-300 ease-in-out pointer-events-auto",
               isRightCollapsed ? "translate-x-full" : "translate-x-0"
             )}>
                {/* Tactical Corner Accents */}
                <div className="absolute top-0 left-0 w-1 h-1 border-t border-l border-lcd-green" />
                <div className="absolute top-0 right-0 w-1 h-1 border-t border-r border-lcd-green" />
                <div className="absolute bottom-0 left-0 w-1 h-1 border-b border-l border-lcd-green" />
                <div className="absolute bottom-0 right-0 w-1 h-1 border-b border-r border-lcd-green" />

                <div>
                   <div className="flex items-center gap-2 mb-3 text-lcd-green uppercase tracking-widest font-bold text-xs">
                      <Layers className="h-4 w-4" />
                      <span>Layer Control</span>
                   </div>
                   <div className="flex flex-col gap-2">
                      {(['satellite', 'vector', 'thermal'] as const).map((layer) => (
                        <button 
                          key={layer}
                          onClick={() => setActiveLayer(layer)}
                          className={cn(
                            "w-full px-3 py-2 text-[10px] uppercase tracking-tighter text-left border transition-all font-lcd",
                            activeLayer === layer 
                              ? "bg-lcd-green text-industrial-bg border-lcd-green shadow-[0_0_10px_rgba(182,255,176,0.3)]" 
                              : "bg-black/40 text-lcd-green/60 border-lcd-green/20 hover:border-lcd-green/50"
                          )}
                        >
                          {layer} Mode
                        </button>
                      ))}
                   </div>
                </div>

                <div className="flex flex-col gap-2">
                   <div className="flex items-center gap-2 mb-3 text-lcd-green uppercase tracking-widest font-bold text-xs">
                      <Settings className="h-4 w-4" />
                      <span>View Tools</span>
                   </div>
                   <div className="grid grid-cols-2 gap-2">
                      <button className="p-2 border border-lcd-green/30 bg-black/40 text-lcd-green hover:bg-lcd-green hover:text-industrial-bg transition-all flex flex-col items-center justify-center gap-1 group">
                         <ZoomIn size={16} className="group-hover:scale-110 transition-transform" />
                         <span className="text-[8px] uppercase font-lcd">Zoom+</span>
                      </button>
                      <button className="p-2 border border-lcd-green/30 bg-black/40 text-lcd-green hover:bg-lcd-green hover:text-industrial-bg transition-all flex flex-col items-center justify-center gap-1 group">
                         <ZoomOut size={16} className="group-hover:scale-110 transition-transform" />
                         <span className="text-[8px] uppercase font-lcd">Zoom-</span>
                      </button>
                      <button className="p-2 border border-lcd-green/30 bg-black/40 text-lcd-green hover:bg-lcd-green hover:text-industrial-bg transition-all flex flex-col items-center justify-center gap-1 group">
                         <Maximize size={16} className="group-hover:scale-110 transition-transform" />
                         <span className="text-[8px] uppercase font-lcd">Full</span>
                      </button>
                      <button className="p-2 border border-lcd-green/30 bg-black/40 text-lcd-green hover:bg-lcd-green hover:text-industrial-bg transition-all flex flex-col items-center justify-center gap-1 group">
                         <Navigation size={16} className="group-hover:scale-110 transition-transform" />
                         <span className="text-[8px] uppercase font-lcd">Center</span>
                      </button>
                   </div>
                </div>

                <div className="mt-auto p-3 border border-red-500/30 bg-red-500/10 text-red-500 flex items-center gap-2 text-[10px] uppercase font-bold animate-pulse">
                       <ShieldAlert className="h-4 w-4" />
                       <span>Anomaly Filter: Active</span>
                </div>
             </div>
          </div>

          {/* BOTTOM HUD: Coordinate readouts and system logs */}
          <div className="absolute bottom-0 left-0 right-0 z-20 p-4 pointer-events-none">
             <div className="flex justify-between items-end gap-4">
                <div className="bg-industrial-panel/80 border border-lcd-green/30 px-4 py-2 backdrop-blur-md shadow-2xl flex items-center gap-6 text-lcd-green font-lcd">
                   <div className="flex flex-col">
                      <span className="text-[8px] uppercase opacity-40 leading-none mb-1">Latitude</span>
                      <span className="text-sm font-bold tabular-nums">40.7128° N</span>
                   </div>
                   <div className="w-px h-8 bg-lcd-green/20" />
                   <div className="flex flex-col">
                      <span className="text-[8px] uppercase opacity-40 leading-none mb-1">Longitude</span>
                      <span className="text-sm font-bold tabular-nums">74.0060° W</span>
                   </div>
                   <div className="w-px h-8 bg-lcd-green/20" />
                   <div className="flex flex-col">
                      <span className="text-[8px] uppercase opacity-40 leading-none mb-1">Altitude</span>
                      <span className="text-sm font-bold tabular-nums">12.4m</span>
                   </div>
                </div>

                <div className="bg-industrial-panel/80 border border-lcd-green/30 px-4 py-2 backdrop-blur-md shadow-2xl text-lcd-green font-lcd text-right">
                   <div className="text-[8px] uppercase opacity-40 leading-none mb-1">Network Latency</div>
                   <div className="text-sm font-bold tabular-nums">24ms <span className="text-[10px] opacity-60">Ping</span></div>
                </div>
             </div>
          </div>

          {/* The actual Map with WebGL overlay */}
          <div className="w-full h-full absolute inset-0 z-0">
            <DynamicTrafficMap />
          </div>

        </div>
      </DashboardShell>
    </AuthGuard>
  );
};

export default LiveMapPage;