'use client';

import React, { useEffect, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import { useRouter } from 'next/navigation';
import { cn } from '@/lib/utils';
import { Layers, Activity, AlertTriangle, Video, Map as MapIcon, ChevronRight, X, Zap, Loader2 } from 'lucide-react';
import { WebSocketMessageType } from '@/lib/websocket/WebSocketClient';
import { useWebSocket } from '@/lib/websocket/WebSocketProvider';

// Fix for default marker icons in Leaflet with Next.js
const fixLeafletIcons = () => {
  // @ts-ignore
  delete L.Icon.Default.prototype._getIconUrl;
  L.Icon.Default.mergeOptions({
    iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
    iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
    shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
  });
};

const LeafletMap: React.FC = () => {
  const mapRef = useRef<HTMLDivElement>(null);
  const leafletMap = useRef<L.Map | null>(null);
  const wsClient = useWebSocket();
  
  // Layer Groups
  const feedsLayer = useRef<L.LayerGroup>(L.layerGroup());
  const congestionLayer = useRef<L.LayerGroup>(L.layerGroup());
  const incidentsLayer = useRef<L.LayerGroup>(L.layerGroup());
  const roadNetworkLayer = useRef<L.LayerGroup>(L.layerGroup());
  const signalsLayer = useRef<L.LayerGroup>(L.layerGroup());
  
  const markersRef = useRef<Record<string, L.Marker>>({});
  const segmentRef = useRef<Record<string, L.Polyline>>({});
  const incidentMarkersRef = useRef<Record<string, L.Marker>>({});
  const signalMarkersRef = useRef<Record<string, L.Marker>>({});

  const { feeds, nodeCongestionData, alerts, sendMessage } = useRealtimeUpdates();
  const router = useRouter();
  
  const [activeLayers, setActiveLayers] = useState({
    feeds: true,
    congestion: true,
    incidents: true,
    roads: true,
    signals: true
  });
  const [showLayerControl, setShowLayerControl] = useState(false);
  const [selectedNode, setSelectedNode] = useState<any>(null);
  const [signalStates, setSignalStates] = useState<Record<string, any>>({});
  const [isChangingPhase, setIsChangingPhase] = useState(false);

  useEffect(() => {
    fixLeafletIcons();

    if (mapRef.current && !leafletMap.current) {
      // Define world bounds to prevent horizontal repetition
      const corner1 = L.latLng(-90, -180);
      const corner2 = L.latLng(90, 180);
      const bounds = L.latLngBounds(corner1, corner2);

      leafletMap.current = L.map(mapRef.current, {
        center: [34.02, -118.02],
        zoom: 13,
        minZoom: 2,
        maxBounds: bounds,
        maxBoundsViscosity: 1.0,
        zoomControl: false,
        attributionControl: false,
      });

      L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; OpenStreetMap &copy; CARTO',
        subdomains: 'abcd',
        maxZoom: 20,
        noWrap: true,
        bounds: bounds
      }).addTo(leafletMap.current);

      feedsLayer.current.addTo(leafletMap.current);
      congestionLayer.current.addTo(leafletMap.current);
      incidentsLayer.current.addTo(leafletMap.current);
      roadNetworkLayer.current.addTo(leafletMap.current);
      signalsLayer.current.addTo(leafletMap.current);

      L.control.zoom({ position: 'bottomright' }).addTo(leafletMap.current);
      
      // Subscribe to signal updates
      const unsubscribe = wsClient.subscribe(WebSocketMessageType.SIGNAL_UPDATE, (data: any) => {
          if (data?.signal_data) {
              setSignalStates(prev => ({
                  ...prev,
                  [data.signal_data.signal_id]: data.signal_data
              }));
          }
      });

      setTimeout(() => leafletMap.current?.invalidateSize(), 100);
      return () => {
          unsubscribe();
          leafletMap.current?.remove();
          leafletMap.current = null;
      };
    }
  }, [wsClient]);

  // Sync layer visibility
  useEffect(() => {
    if (!leafletMap.current) return;
    activeLayers.feeds ? feedsLayer.current.addTo(leafletMap.current) : feedsLayer.current.remove();
    activeLayers.congestion ? congestionLayer.current.addTo(leafletMap.current) : congestionLayer.current.remove();
    activeLayers.incidents ? incidentsLayer.current.addTo(leafletMap.current) : incidentsLayer.current.remove();
    activeLayers.roads ? roadNetworkLayer.current.addTo(leafletMap.current) : roadNetworkLayer.current.remove();
    activeLayers.signals ? signalsLayer.current.addTo(leafletMap.current) : signalsLayer.current.remove();
  }, [activeLayers]);

  // Update Node Congestion, Road Network & Signals
  useEffect(() => {
    if (!leafletMap.current || !nodeCongestionData) return;

    nodeCongestionData.forEach(node => {
      const { id, latitude: lat, longitude: lon, congestion_score: score, signal_id } = node;
      const color = score > 0.7 ? '#ff3e3e' : score > 0.4 ? '#facc15' : '#00ff41';
      const size = 10 + (score * 10);

      const icon = L.divIcon({
        className: 'congestion-node',
        html: `<div style="width: ${size}px; height: ${size}px; background-color: ${color}; border-radius: 50%; border: 2px solid rgba(0,0,0,0.5); box-shadow: 0 0 10px ${color}; transition: all 0.5s ease;"></div>`,
        iconSize: [size, size],
        iconAnchor: [size/2, size/2]
      });

      if (markersRef.current[id]) {
        markersRef.current[id].setLatLng([lat, lon]);
        markersRef.current[id].setIcon(icon);
      } else {
        const marker = L.marker([lat, lon], { icon }).addTo(congestionLayer.current);
        marker.on('click', () => setSelectedNode(node));
        markersRef.current[id] = marker;
      }

      // Draw Signal Markers if applicable
      if (signal_id) {
          const state = signalStates[signal_id];
          const phaseColor = state?.current_phase?.includes('GREEN') ? '#00ff41' : 
                            state?.current_phase?.includes('YELLOW') ? '#facc15' : 
                            state?.current_phase?.includes('RED') ? '#ff3e3e' : '#555';
          
          const signalIcon = L.divIcon({
              className: 'signal-marker',
              html: `<div style="display: flex; flex-direction: column; gap: 2px; padding: 3px; background: #222; border: 1px solid #444; border-radius: 4px; box-shadow: 0 0 10px rgba(0,0,0,0.5);">
                <div style="width: 8px; height: 8px; border-radius: 50%; background: ${state?.current_phase?.includes('RED') ? '#ff3e3e' : '#111'};"></div>
                <div style="width: 8px; height: 8px; border-radius: 50%; background: ${state?.current_phase?.includes('YELLOW') ? '#facc15' : '#111'};"></div>
                <div style="width: 8px; height: 8px; border-radius: 50%; background: ${state?.current_phase?.includes('GREEN') ? '#00ff41' : '#111'};"></div>
              </div>`,
              iconSize: [16, 32],
              iconAnchor: [-10, 16] // Offset from node
          });

          if (signalMarkersRef.current[signal_id]) {
              signalMarkersRef.current[signal_id].setLatLng([lat, lon]);
              signalMarkersRef.current[signal_id].setIcon(signalIcon);
          } else {
              const marker = L.marker([lat, lon], { icon: signalIcon }).addTo(signalsLayer.current);
              signalMarkersRef.current[signal_id] = marker;
          }
      }
    });

    // Draw road segments (same grid logic as before)
    const nodeMap = new Map();
    nodeCongestionData.forEach(n => nodeMap.set(n.id, n));
    nodeCongestionData.forEach(u => {
      const coords = u.id.split(',').map(Number);
      if (coords.length === 2) {
        const [i, j] = coords;
        [`${i + 1},${j}`, `${i},${j + 1}`].forEach(vId => {
          const v = nodeMap.get(vId);
          if (v) {
            const edgeId = `${u.id}-${v.id}`;
            const avgScore = ((u.congestion_score || 0) + (v.congestion_score || 0)) / 2;
            const color = avgScore > 0.7 ? '#ff3e3e' : avgScore > 0.4 ? '#facc15' : '#00ff41';
            if (segmentRef.current[edgeId]) segmentRef.current[edgeId].setStyle({ color, weight: 4 + avgScore * 6 });
            else segmentRef.current[edgeId] = L.polyline([[u.latitude, u.longitude], [v.latitude, v.longitude]], { color, weight: 4, opacity: 0.6 }).addTo(roadNetworkLayer.current);
          }
        });
      }
    });
  }, [nodeCongestionData, signalStates]);

  const handleSetPhase = (signalId: string, phase: string) => {
      setIsChangingPhase(true);
      sendMessage(WebSocketMessageType.SET_SIGNAL_PHASE as any, {
          signal_id: signalId,
          phase: phase,
          duration_seconds: 30
      });
      // Visual feedback
      setTimeout(() => setIsChangingPhase(false), 1000);
  };

  return (
    <div className="w-full h-full relative bg-[#0a0a0a] overflow-hidden">
      <div ref={mapRef} className="absolute inset-0 z-0" />
      
      {/* HUD Overlays */}
      <div className="absolute top-4 right-4 z-[1002] flex flex-col gap-2">
        <button onClick={() => setShowLayerControl(!showLayerControl)} className="bg-black/80 border border-[#00ff41]/30 p-2 text-[#00ff41] hover:bg-[#00ff41]/10 transition-colors">
          <Layers size={20} />
        </button>
        {showLayerControl && (
          <div className="bg-black/90 border border-[#00ff41]/30 p-4 w-48 font-mono text-xs text-[#00ff41] flex flex-col gap-3 shadow-2xl">
            <div className="flex justify-between items-center mb-1 pb-1 border-b border-[#00ff41]/20">
              <span className="font-bold uppercase">Intelligence Layers</span>
              <X size={14} className="cursor-pointer" onClick={() => setShowLayerControl(false)} />
            </div>
            {[
              { id: 'feeds', icon: Video, label: 'Surveillance Nodes' },
              { id: 'congestion', icon: Activity, label: 'Congestion Heat' },
              { id: 'roads', icon: MapIcon, label: 'Network Topology' },
              { id: 'signals', icon: Zap, label: 'Signal Controllers' },
              { id: 'incidents', icon: AlertTriangle, label: 'Incident Events' }
            ].map(layer => (
                <label key={layer.id} className="flex items-center gap-3 cursor-pointer group">
                  <input type="checkbox" checked={(activeLayers as any)[layer.id]} onChange={() => setActiveLayers(prev => ({ ...prev, [layer.id]: !(prev as any)[layer.id] }))} className="hidden" />
                  <div className={cn("w-3 h-3 border border-[#00ff41]/50", (activeLayers as any)[layer.id] && "bg-[#00ff41]")}></div>
                  <layer.icon size={14} className="opacity-60" />
                  <span className={cn("group-hover:translate-x-1 transition-transform", !(activeLayers as any)[layer.id] && "opacity-40")}>{layer.label}</span>
                </label>
            ))}
          </div>
        )}
      </div>

      {/* Node & Signal Info Panel */}
      {selectedNode && (
        <div className="absolute bottom-4 right-4 z-[1002] bg-black/95 border-2 border-[#00ff41] p-4 w-72 font-mono text-[#00ff41] shadow-[0_0_30px_rgba(0,255,65,0.2)]">
          <div className="flex justify-between items-start mb-4">
            <div>
              <h3 className="text-sm font-bold uppercase tracking-tighter">Telemetric Uplink</h3>
              <p className="text-[10px] opacity-60">NODE: {selectedNode.id}</p>
            </div>
            <X size={16} className="cursor-pointer hover:text-white" onClick={() => setSelectedNode(null)} />
          </div>
          
          <div className="space-y-4">
            <div className="bg-[#00ff41]/5 p-3 border border-[#00ff41]/20 relative overflow-hidden">
                <div className="absolute top-0 left-0 w-1 h-full bg-[#00ff41]"></div>
                <div className="flex justify-between items-center text-xs mb-1">
                  <span className="opacity-60 uppercase font-bold">Saturation</span>
                  <span className={cn("font-bold", selectedNode.congestion_score > 0.7 ? "text-[#ff3e3e]" : "text-[#00ff41]")}>
                    {(selectedNode.congestion_score * 100).toFixed(1)}%
                  </span>
                </div>
                <div className="w-full bg-[#00ff41]/10 h-1 rounded-full overflow-hidden">
                  <div className="bg-[#00ff41] h-full transition-all duration-1000" style={{ width: `${selectedNode.congestion_score * 100}%` }} />
                </div>
            </div>

            {selectedNode.signal_id ? (
                <div className="space-y-2">
                    <div className="flex items-center gap-2 text-[10px] uppercase font-bold text-[#00ff41]/80">
                        <Zap size={12} /> Signal Controller: {selectedNode.signal_id}
                    </div>
                    <div className="grid grid-cols-3 gap-1">
                        {[
                            { phase: 'GREEN', color: 'bg-[#00ff41]/20 border-[#00ff41] text-[#00ff41]' },
                            { phase: 'YELLOW', color: 'bg-[#facc15]/20 border-[#facc15] text-[#facc15]' },
                            { phase: 'RED', color: 'bg-[#ff3e3e]/20 border-[#ff3e3e] text-[#ff3e3e]' }
                        ].map(p => (
                            <button 
                                key={p.phase}
                                disabled={isChangingPhase}
                                onClick={() => handleSetPhase(selectedNode.signal_id, p.phase)}
                                className={cn(
                                    "text-[9px] py-1 border transition-all hover:scale-105 active:scale-95",
                                    p.color,
                                    signalStates[selectedNode.signal_id]?.current_phase?.includes(p.phase) && "ring-2 ring-white ring-inset border-transparent",
                                    isChangingPhase && "opacity-50 cursor-not-allowed"
                                )}
                            >
                                {isChangingPhase && signalStates[selectedNode.signal_id]?.current_phase?.includes(p.phase) ? (
                                    <Loader2 size={10} className="animate-spin mx-auto" />
                                ) : p.phase}
                            </button>
                        ))}
                    </div>
                    <p className="text-[8px] opacity-40 text-center italic">Phase modification will affect node saturation in real-time.</p>
                </div>
            ) : (
                <div className="text-[9px] opacity-40 italic border border-white/5 p-2 bg-white/5 text-center">
                    No active controller detected at this junction.
                </div>
            )}
            
            <div className="grid grid-cols-2 gap-2 text-[10px]">
              <div className="bg-black border border-[#00ff41]/20 p-2">
                <p className="opacity-40 uppercase text-[8px]">Avg Velocity</p>
                <p className="text-sm font-bold">{selectedNode.average_speed?.toFixed(1) || 0} <span className="text-[8px] opacity-40">KM/H</span></p>
              </div>
              <div className="bg-black border border-[#00ff41]/20 p-2">
                <p className="opacity-40 uppercase text-[8px]">Inflow Rate</p>
                <p className="text-sm font-bold">{selectedNode.vehicle_count || 0} <span className="text-[8px] opacity-40">VPH</span></p>
              </div>
            </div>
          </div>
        </div>
      )}
      
      {/* Legend */}
      <div className="absolute bottom-6 left-6 z-[1001] bg-black/90 border border-[#00ff41]/30 p-3 font-mono text-[9px] text-[#00ff41] flex flex-col gap-2 shadow-2xl backdrop-blur-md">
        <div className="flex items-center gap-2 mb-1 border-b border-[#00ff41]/20 pb-1 uppercase font-bold opacity-60">
          <span>Map Legend</span>
        </div>
        <div className="grid grid-cols-2 gap-x-4 gap-y-1">
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-[#ff3e3e] shadow-[0_0_5px_#ff3e3e]"></div>
              <span>CRITICAL</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-[#00ff41] shadow-[0_0_5px_#00ff41]"></div>
              <span>OPTIMAL</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="w-3 h-3 border border-[#ff3e3e] rotate-45 flex items-center justify-center text-[7px] font-bold text-[#ff3e3e]">!</div>
              <span>INCIDENT</span>
            </div>
            <div className="flex items-center gap-2">
              <div className="flex flex-col gap-[1px]">
                  <div className="w-1.5 h-1.5 bg-[#00ff41] rounded-full"></div>
                  <div className="w-1.5 h-1.5 bg-[#ff3e3e] rounded-full"></div>
              </div>
              <span>SIGNAL</span>
            </div>
        </div>
      </div>
      
      {/* Matrix Overlay Effect */}
      <div className="absolute inset-0 pointer-events-none border-[1px] border-[#00ff41]/10 z-[1000] shadow-[inset_0_0_50px_rgba(0,255,65,0.05)]" />
    </div>
  );
};

export default LeafletMap;
