'use client';

import React, { useEffect, useRef, useState, useImperativeHandle, forwardRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import { useRouter } from 'next/navigation';
import { cn } from '@/lib/utils';
import { Layers, Activity, AlertTriangle, Video, Map as MapIcon, ChevronRight, X, Zap, Loader2, Search } from 'lucide-react';
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

const LeafletMap = forwardRef(({ activeLayer, activeLayers }: { 
  activeLayer: 'satellite' | 'vector' | 'thermal';
  activeLayers: Record<string, boolean>;
}, ref) => {
  const mapRef = useRef<HTMLDivElement>(null);
  const leafletMap = useRef<L.Map | null>(null);
  const wsClient = useWebSocket();

  useImperativeHandle(ref, () => ({
    zoomIn: () => {
      leafletMap.current?.zoomIn();
    },
    zoomOut: () => {
      leafletMap.current?.zoomOut();
    },
    center: () => {
      leafletMap.current?.setView([34.02, -118.02]);
    },
    invalidateSize: () => {
      leafletMap.current?.invalidateSize();
    }
  }));

  // Define custom icon to avoid default asset 404s in Next.js
  const defaultIcon = useRef(L.icon({
    iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
    shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
    iconSize: [25, 41],
    iconAnchor: [12, 41],
    popupAnchor: [1, -34],
    shadowSize: [41, 41],
  })).current;

  // Layer Groups
  const feedsLayer = useRef<L.LayerGroup>(L.layerGroup());
  const congestionLayer = useRef<L.LayerGroup>(L.layerGroup());
  const incidentsLayer = useRef<L.LayerGroup>(L.layerGroup());
  const roadNetworkLayer = useRef<L.LayerGroup>(L.layerGroup());
  const signalsLayer = useRef<L.LayerGroup>(L.layerGroup());

  const tileLayerRef = useRef<L.TileLayer | null>(null);
  const feedMarkersRef = useRef<Record<string, L.Marker>>({});
  const congestionMarkersRef = useRef<Record<string, L.Marker>>({});
  const segmentRef = useRef<Record<string, L.Polyline>>({});
  const incidentMarkersRef = useRef<Record<string, L.Marker>>({});
  const signalMarkersRef = useRef<Record<string, L.Marker>>({});

  const { feeds, nodeCongestionData, alerts, sendMessage } = useRealtimeUpdates();
  const router = useRouter();

  const [selectedNode, setSelectedNode] = useState<any>(null);
  const [signalStates, setSignalStates] = useState<Record<string, any>>({});
  const [isChangingPhase, setIsChangingPhase] = useState(false);

  // Search State
  interface SearchResult {
    type: 'feed' | 'node';
    data: any;
    label: string;
  }
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);

  useEffect(() => {
    if (!mapRef.current || leafletMap.current) return;

    let map: L.Map;
    let resizeObserver: ResizeObserver | null = null;

    try {
      const corner1 = L.latLng(-90, -180);
      const corner2 = L.latLng(90, 180);
      const bounds = L.latLngBounds(corner1, corner2);

      map = L.map(mapRef.current, {
        center: [34.02, -118.02],
        zoom: 13,
        minZoom: 2,
        maxBounds: bounds,
        maxBoundsViscosity: 1.0,
        zoomControl: false,
        attributionControl: false,
      });

      const centerMarker = L.marker([34.02, -118.02], { 
        icon: defaultIcon,
        draggable: true 
      }).addTo(map).bindPopup('CENTER POINT');

      centerMarker.on('dragend', (e) => {
        const position = e.target.getLatLng();
        map.panTo(position);
      });

      feedsLayer.current.addTo(map);
      congestionLayer.current.addTo(map);
      incidentsLayer.current.addTo(map);
      roadNetworkLayer.current.addTo(map);
      signalsLayer.current.addTo(map);

      leafletMap.current = map;

      // Initialize Tile Layer immediately
      const layers: Record<string, { url: string; attribution: string }> = {
        vector: {
          url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
          attribution: '&copy; OpenStreetMap contributors',
        },
        satellite: {
          url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
          attribution: 'Tiles &copy; Esri &mdash; Source: Esri, iBird, USDA, USGS, ASTER, World Imaging with imagery rights',
        },
        thermal: {
          url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
          attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
        },
      };
      const selected = layers[activeLayer];
      tileLayerRef.current = L.tileLayer(selected.url, {
        attribution: selected.attribution,
        maxZoom: 19,
        crossOrigin: true
      }).addTo(map);

      resizeObserver = new ResizeObserver(() => {
        map.invalidateSize();
      });
      resizeObserver.observe(mapRef.current!);
      
      // Initial force resize
      setTimeout(() => map.invalidateSize(), 100);

    } catch (error) {
      console.error("Leaflet initialization failed:", error);
    }

    const unsubscribe = wsClient.subscribe(WebSocketMessageType.SIGNAL_UPDATE, (data: any) => {
      if (data?.signal_data) {
        setSignalStates(prev => ({
          ...prev,
          [data.signal_data.signal_id]: data.signal_data
        }));
      }
    });

    return () => {
      unsubscribe();
      if (resizeObserver) {
        resizeObserver.disconnect();
      }
      if (leafletMap.current) {
        leafletMap.current.remove();
        leafletMap.current = null;
      }
    };
  }, [wsClient]);

  useEffect(() => {
    if (!leafletMap.current || !mapRef.current) return;

    const layers: Record<string, { url: string; attribution: string }> = {
      vector: {
        url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
        attribution: '&copy; OpenStreetMap contributors',
      },
      satellite: {
        url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        attribution: 'Tiles &copy; Esri &mdash; Source: Esri, iBird, USDA, USGS, ASTER, World Imaging with imagery rights',
      },
      thermal: {
        url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
        attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
      },
    };

    const selected = layers[activeLayer];

    if (tileLayerRef.current) {
      tileLayerRef.current.remove();
    }

    try {
      tileLayerRef.current = L.tileLayer(selected.url, {
        attribution: selected.attribution,
        maxZoom: 19,
        crossOrigin: true
      }).addTo(leafletMap.current);
      
      setTimeout(() => {
        leafletMap.current?.invalidateSize();
      }, 100);
    } catch (e) {
      console.warn("Failed to update tile layer:", e);
    }
  }, [activeLayer]);
  useEffect(() => {
    if (!leafletMap.current) return;
    activeLayers.feeds ? feedsLayer.current.addTo(leafletMap.current) : feedsLayer.current.remove();
    activeLayers.congestion ? congestionLayer.current.addTo(leafletMap.current) : congestionLayer.current.remove();
    activeLayers.incidents ? incidentsLayer.current.addTo(leafletMap.current) : incidentsLayer.current.remove();
    activeLayers.roads ? roadNetworkLayer.current.addTo(leafletMap.current) : roadNetworkLayer.current.remove();
    activeLayers.signals ? signalsLayer.current.addTo(leafletMap.current) : signalsLayer.current.remove();
  }, [activeLayers]);

  useEffect(() => {
    if (!leafletMap.current || !feeds) return;

    feeds.forEach(feed => {
      const lat = feed.config?.latitude || feed.latitude;
      const lng = feed.config?.longitude || feed.longitude;

      if (lat && lng) {
        const id = feed.feed_id;
        const statusColor = feed.status === 'running' ? '#00ff41' : feed.status === 'error' ? '#ff3e3e' : '#facc15';

        const icon = L.divIcon({
          className: 'feed-marker',
          html: `<div style="
            display: flex; 
            align-items: center; 
            justify-content: center; 
            width: 24px; 
            height: 24px; 
            background-color: #000; 
            border: 2px solid ${statusColor}; 
            border-radius: 4px; 
            box-shadow: 0 0 10px ${statusColor};
          ">
            <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="${statusColor}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m22 8-6 4 6 4V8Z"/><rect width="14" height="12" x="2" y="6" rx="2" ry="2"/></svg>
          </div>`,
          iconSize: [24, 24],
          iconAnchor: [12, 12]
        });

        if (feedMarkersRef.current[id]) {
          feedMarkersRef.current[id].setLatLng([lat, lng]);
          feedMarkersRef.current[id].setIcon(icon);
        } else {
          const marker = L.marker([lat, lng], { icon }).addTo(feedsLayer.current);
          marker.bindPopup(`
            <div style="font-family: monospace; color: #00ff41; background: #000; padding: 5px;">
              <div style="font-weight: bold; border-bottom: 1px solid #333; margin-bottom: 5px;">${feed.name || feed.feed_id}</div>
              <div style="font-size: 10px; opacity: 0.8;">${feed.source}</div>
              <div style="font-size: 10px; margin-top: 5px;">STATUS: <span style="color: ${statusColor}">${feed.status.toUpperCase()}</span></div>
            </div>
          `);
          feedMarkersRef.current[id] = marker;
        }
      }
    });
  }, [feeds]);

  useEffect(() => {
    if (!leafletMap.current || !nodeCongestionData) return;

    nodeCongestionData.forEach(node => {
      const { id, latitude: lat, longitude: lon, congestion_score, signal_id } = node;
      const score = congestion_score || 0;
      const color = score > 0.7 ? '#ff3e3e' : score > 0.4 ? '#facc15' : '#00ff41';
      const size = 10 + (score * 10);

      const icon = L.divIcon({
        className: 'congestion-node',
        html: `<div style="width: ${size}px; height: ${size}px; background-color: ${color}; border-radius: 50%; border: 2px solid rgba(0,0,0,0.5); box-shadow: 0 0 10px ${color}; transition: all 0.5s ease;"></div>`,
        iconSize: [size, size],
        iconAnchor: [size / 2, size / 2]
      });

      if (congestionMarkersRef.current[id]) {
        congestionMarkersRef.current[id].setLatLng([lat, lon]);
        congestionMarkersRef.current[id].setIcon(icon);
      } else {
        const marker = L.marker([lat, lon], { icon }).addTo(congestionLayer.current);
        marker.on('click', () => setSelectedNode(node));
        congestionMarkersRef.current[id] = marker;
      }

      if (signal_id) {
        const state = signalStates[signal_id];
        const signalIcon = L.divIcon({
          className: 'signal-marker',
          html: `<div style="display: flex; flex-direction: column; gap: 2px; padding: 3px; background: #222; border: 1px solid #444; border-radius: 4px; box-shadow: 0 0 10px rgba(0,0,0,0.5);">
            <div style="width: 8px; height: 8px; border-radius: 50%; background: ${state?.current_phase?.includes('RED') ? '#ff3e3e' : '#111'};"></div>
            <div style="width: 8px; height: 8px; border-radius: 50%; background: ${state?.current_phase?.includes('YELLOW') ? '#facc15' : '#111'};"></div>
            <div style="width: 8px; height: 8px; border-radius: 50%; background: ${state?.current_phase?.includes('GREEN') ? '#00ff41' : '#111'};"></div>
          </div>`,
          iconSize: [16, 32],
          iconAnchor: [-10, 16]
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
            if (segmentRef.current[edgeId]) {
              segmentRef.current[edgeId].setStyle({ color, weight: 4 + avgScore * 6 });
            } else {
              segmentRef.current[edgeId] = L.polyline(
                [[u.latitude, u.longitude], [v.latitude, v.longitude]],
                { color, weight: 4, opacity: 0.6 }
              ).addTo(roadNetworkLayer.current);
            }
          }
        });
      }
    });
  }, [nodeCongestionData, signalStates]);

  const handleSetPhase = (signalId: string, phase: string) => {
    setIsChangingPhase(true);
    sendMessage(WebSocketMessageType.SET_SIGNAL_PHASE, {
      signal_id: signalId,
      phase: phase,
      duration_seconds: 30
    });
    setTimeout(() => setIsChangingPhase(false), 1000);
  };

  const handleSearch = (query: string) => {
    setSearchQuery(query);
    if (!query.trim()) {
      setSearchResults([]);
      return;
    }

    const lowerQuery = query.toLowerCase();
    const results: SearchResult[] = [];

    if (feeds) {
      feeds.forEach(feed => {
        if (
          (feed.name || feed.feed_id).toLowerCase().includes(lowerQuery) ||
          feed.source.toLowerCase().includes(lowerQuery)
        ) {
          results.push({ type: 'feed', data: feed, label: feed.name || feed.feed_id });
        }
      });
    }

    if (nodeCongestionData) {
      nodeCongestionData.forEach(node => {
        if (
          node.id.toLowerCase().includes(lowerQuery) ||
          node.name?.toLowerCase().includes(lowerQuery)
        ) {
          results.push({ type: 'node', data: node, label: `NODE: ${node.id}` });
        }
      });
    }

    setSearchResults(results.slice(0, 10));
  };

  const selectResult = (result: any) => {
    setSearchQuery(result.label);
    setSearchResults([]);

    let lat, lng;
    if (result.type === 'feed') {
      lat = result.data.config?.latitude || result.data.latitude;
      lng = result.data.config?.longitude || result.data.longitude;
    } else if (result.type === 'node') {
      lat = result.data.latitude;
      lng = result.data.longitude;
      setSelectedNode(result.data);
    }

    if (lat && lng && leafletMap.current) {
      leafletMap.current.flyTo([lat, lng], 18, { duration: 1.5 });
    }
  };

  return (
    <div className="relative w-full h-full bg-[#0a0a0a] overflow-hidden">
      <div ref={mapRef} className="absolute inset-0 z-0" />

      {/* Search Bar */}
      <div className="absolute top-20 left-4 z-[1002] w-72 font-mono">
        <div className="relative group">
          <div className="absolute inset-y-0 left-0 flex items-center pl-3 pointer-events-none">
            <Search className="w-4 h-4 text-[#00ff41]/50 group-focus-within:text-[#00ff41]" />
          </div>
          <input
            type="text"
            className="block w-full p-2 pl-10 text-xs text-[#00ff41] bg-black/80 border border-[#00ff41]/30 focus:ring-1 focus:ring-[#00ff41] focus:border-[#00ff41] placeholder-[#00ff41]/30 backdrop-blur-sm"
            placeholder="QUERY FEED OR NODE ID..."
            value={searchQuery}
            onChange={(e) => handleSearch(e.target.value)}
            onFocus={() => setIsSearching(true)}
            onBlur={() => setTimeout(() => setIsSearching(false), 200)}
          />
        </div>

        {searchResults.length > 0 && isSearching && (
          <div className="absolute mt-1 w-full bg-black/95 border border-[#00ff41]/30 shadow-xl max-h-60 overflow-y-auto">
            {searchResults.map((result, idx) => (
              <button
                key={idx}
                className="w-full text-left px-4 py-2 text-xs text-[#00ff41] hover:bg-[#00ff41]/20 flex items-center justify-between group border-b border-[#00ff41]/10 last:border-0"
                onClick={() => selectResult(result)}
              >
                <div className="flex items-center gap-2">
                  {result.type === 'feed' ? <Video size={12} /> : <Activity size={12} />}
                  <span className="truncate">{result.label}</span>
                </div>
                <ChevronRight size={10} className="opacity-0 group-hover:opacity-100 transition-opacity" />
              </button>
            ))}
          </div>
        )}
      </div>

      {/* HUD Overlays */}
      <div className="absolute top-4 right-4 z-[1002] flex flex-col gap-2">
        {/* Layer Control removed and moved to TOP HUD in page.tsx */}
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
          <div className="flex items-center gap-2">
            <div className="w-3 h-3 border border-[#00ff41] rounded-sm flex items-center justify-center text-[7px] font-bold text-[#00ff41]">
              <div className="w-1.5 h-1.5 bg-[#00ff41] rounded-sm"></div>
            </div>
            <span>FEED</span>
          </div>
        </div>
      </div>

      {/* Matrix Overlay Effect */}
      <div className="absolute inset-0 pointer-events-none border-[1px] border-[#00ff41]/10 z-20 shadow-[inset_0_0_50px_rgba(0,255,65,0.05)]" />
    </div>
  );
});

export default LeafletMap;
