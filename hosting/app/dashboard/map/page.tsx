"use client";

import React, { useEffect, useRef, useState } from 'react';
import dynamic from 'next/dynamic';
import 'leaflet/dist/leaflet.css';
import { Activity, Layers, Video, Map as MapIcon, Zap, AlertTriangle, X, ZoomIn, ZoomOut, Navigation, Maximize, Check, Crosshair, Target, MapPin, Search } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useVehicleTracking } from '@/lib/hooks/useVehicleTracking';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import { feedService } from '@/lib/services/feedService';
import { geocodeService, GeocodeResult } from '@/lib/services/geocodeService';
import DashboardHeader from '@/components/dashboard/DashboardHeader';
import AuthGuard from '@/components/auth/AuthGuard';
import { UserRole } from '@/lib/auth/roles';

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
  const { vehicles, version: vehicleVersion } = useVehicleTracking();
  const { kpis, feeds: rtFeeds } = useRealtimeUpdates();
  const runningFeeds = (rtFeeds || []).filter(f => f.status === 'running').length;
  const mapRef = useRef<any>(null);
  const [activeLayer, setActiveLayer] = useState<'satellite' | 'vector' | 'thermal'>('satellite');
  const [showLayerControl, setShowLayerControl] = useState(false);
  const [showAllUnits, setShowAllUnits] = useState(false);
  const [calibrating, setCalibrating] = useState(false);
  const [showLocPanel, setShowLocPanel] = useState(false);
  const [locFeedId, setLocFeedId] = useState('');
  const [locQuery, setLocQuery] = useState('');
  const [locResults, setLocResults] = useState<GeocodeResult[]>([]);
  const [locSearching, setLocSearching] = useState(false);
  const [locStatus, setLocStatus] = useState<string | null>(null);

  const locFeeds = rtFeeds || [];
  const locFeed = locFeeds.find(f => f.feed_id === (locFeedId || locFeeds[0]?.feed_id)) || locFeeds[0];
  const locFeedKey = locFeed?.feed_id || '';

  // Debounced address search via the same-origin /api/geocode proxy
  // (server-side Photon/Nominatim, no API key, no browser CORS).
  useEffect(() => {
    if (!showLocPanel) return;
    const q = locQuery.trim();
    const t = setTimeout(async () => {
      if (q.length < 3) { setLocResults([]); return; }
      setLocSearching(true);
      try {
        setLocResults(await geocodeService.search(q));
        setLocStatus(null);
      } catch (e: any) {
        setLocResults([]);
        setLocStatus(e?.message || 'search failed');
      } finally {
        setLocSearching(false);
      }
    }, 350);
    return () => clearTimeout(t);
  }, [locQuery, showLocPanel]);

  const applyLocation = async (r: GeocodeResult) => {
    if (!locFeedKey) return;
    setLocStatus('saving…');
    try {
      await feedService.updateFeedConfig(locFeedKey, { latitude: r.lat, longitude: r.lon });
      // Marker moves on the next WS feed_update broadcast (LeafletMap).
      setLocStatus(`Set ${locFeedKey} → ${r.lat.toFixed(5)}, ${r.lon.toFixed(5)}`);
    } catch (e: any) {
      setLocStatus(`save failed: ${e?.message || e}`);
    }
  };
  const [activeLayers, setActiveLayers] = useState({
    feeds: true,
    congestion: true,
    incidents: true,
    roads: true,
    signals: true,
    vehicles: true
  });

  return (
    <AuthGuard requiredRole={UserRole.VIEWER}>
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
          @keyframes alertPulse {
            0% { box-shadow: 0 0 0 0 rgba(255, 62, 62, 0.7); }
            70% { box-shadow: 0 0 0 14px rgba(255, 62, 62, 0); }
            100% { box-shadow: 0 0 0 0 rgba(255, 62, 62, 0); }
          }
          .alert-pulse { animation: alertPulse 1.6s infinite; }
          @keyframes ringPulse {
            0%, 100% { opacity: 0.9; }
            50% { opacity: 0.45; }
          }
          .feed-ring { animation: ringPulse 2.4s infinite; }
        `}</style>
        {/* TOP HUD: Title and Global Status */}
        <div className="absolute top-0 left-0 right-0 z-20 pointer-events-none px-6 py-4 flex items-center justify-between">
           <div className="flex flex-col bg-industrial-bg/70 backdrop-blur-sm px-3 py-2 border border-lcd-green/30 shadow-lg">
              <h1 className="text-2xl font-bold uppercase tracking-[0.4em] text-lcd-green drop-shadow-[0_0_10px_rgba(182,255,176,0.5)] font-lcd">
                Surveillance Grid
              </h1>
              <span className="text-[10px] text-lcd-green/50 uppercase tracking-widest font-mono">
                Sector: North-01 // Status: Active // Sync: 100%
              </span>
           </div>
           <div className="flex items-center gap-4">
              {/* LIVE KPI STRIP: global aggregates pushed over WS, so the map
                  page gives operators the big picture without leaving it. */}
              <div className="hidden lg:flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-lcd-green">
                <div className="bg-industrial-panel/80 border border-lcd-green/30 px-2 py-1 backdrop-blur-sm shadow-lg" title="Global congestion index (0-100)">
                  <span className="opacity-50">Congestion</span>{' '}
                  <span className={cn('font-bold', (kpis?.congestion_index ?? 0) > 70 ? 'text-[#ff3e3e]' : (kpis?.congestion_index ?? 0) > 40 ? 'text-[#facc15]' : 'text-lcd-green')}>
                    {kpis?.congestion_index != null ? kpis.congestion_index.toFixed(0) : '—'}%
                  </span>
                </div>
                <div className="bg-industrial-panel/80 border border-lcd-green/30 px-2 py-1 backdrop-blur-sm shadow-lg" title="Average speed across all feeds">
                  <span className="opacity-50">Avg Speed</span>{' '}
                  <span className="font-bold">{kpis?.average_speed_kmh != null ? `${kpis.average_speed_kmh.toFixed(1)} km/h` : '—'}</span>
                </div>
                <div className="bg-industrial-panel/80 border border-lcd-green/30 px-2 py-1 backdrop-blur-sm shadow-lg" title="Active incidents">
                  <span className="opacity-50">Incidents</span>{' '}
                  <span className={cn('font-bold', (kpis?.active_incidents_count ?? 0) > 0 ? 'text-[#ff3e3e]' : 'text-lcd-green')}>
                    {kpis?.active_incidents_count ?? '—'}
                  </span>
                </div>
                <div className="bg-industrial-panel/80 border border-lcd-green/30 px-2 py-1 backdrop-blur-sm shadow-lg" title="Global network health score (0-100)">
                  <span className="opacity-50">Health</span>{' '}
                  <span className={cn('font-bold', (kpis?.global_health_score ?? 100) < 50 ? 'text-[#ff3e3e]' : (kpis?.global_health_score ?? 100) < 75 ? 'text-[#facc15]' : 'text-lcd-green')}>
                    {kpis?.global_health_score != null ? kpis.global_health_score.toFixed(0) : '—'}%
                  </span>
                </div>
                <div className="bg-industrial-panel/80 border border-lcd-green/30 px-2 py-1 backdrop-blur-sm shadow-lg" title="Running feeds / total">
                  <span className="opacity-50">Feeds</span>{' '}
                  <span className="font-bold">{runningFeeds}/{rtFeeds?.length ?? 0}</span>
                </div>
              </div>
              <div className="bg-industrial-panel/80 border border-lcd-green/30 px-3 py-1 flex items-center gap-3 text-lcd-green text-[10px] uppercase tracking-tighter backdrop-blur-sm shadow-lg">
                 <div className="flex items-center gap-2">
                   <Activity className="h-3 w-3 animate-pulse" />
                   <span>Real-time Telemetry Active</span>
                 </div>
                 <div className="w-px h-3 bg-lcd-green/30" />
                 <div className="flex items-center gap-1 font-bold">
                   <span>Units:</span>
                   <span>{Object.keys(vehicles.current || {}).length}</span>
                 </div>
              </div>
              
              {/* Layer Control Toggle */}
              <button
                onClick={() => setCalibrating(c => !c)}
                title="Geo-calibrate a feed: click map points + enter image coords"
                className={cn(
                  "border p-2 transition-all backdrop-blur-sm shadow-lg pointer-events-auto",
                  calibrating
                    ? "bg-[#facc15] text-industrial-bg border-[#facc15]"
                    : "bg-industrial-panel/80 border-lcd-green/30 text-lcd-green hover:bg-lcd-green hover:text-industrial-bg"
                )}
              >
                <Target size={16} />
              </button>
              <button
                onClick={() => setShowLocPanel(v => !v)}
                title="Set feed location via address search"
                className={cn(
                  "border p-2 transition-all backdrop-blur-sm shadow-lg pointer-events-auto",
                  showLocPanel
                    ? "bg-[#facc15] text-industrial-bg border-[#facc15]"
                    : "bg-industrial-panel/80 border-lcd-green/30 text-lcd-green hover:bg-lcd-green hover:text-industrial-bg"
                )}
              >
                <MapPin size={16} />
              </button>
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
            <div className="flex flex-col gap-1 pb-1 border-b border-lcd-green/20">
              <span className="font-bold uppercase tracking-widest opacity-60 text-[10px]">Base Layer</span>
              <div className="flex gap-1">
                {(['satellite', 'vector', 'thermal'] as const).map((l) => (
                  <button
                    key={l}
                    onClick={() => setActiveLayer(l)}
                    className={cn(
                      "flex-1 px-2 py-1 text-[10px] uppercase tracking-widest border transition-all",
                      activeLayer === l
                        ? "bg-lcd-green text-industrial-bg border-lcd-green font-bold"
                        : "border-lcd-green/30 opacity-50 hover:opacity-100"
                    )}
                  >
                    {l}
                  </button>
                ))}
              </div>
            </div>
            {[
              { id: 'feeds', icon: Video, label: 'Surveillance Nodes' },
              { id: 'congestion', icon: Activity, label: 'Congestion Heat' },
              { id: 'roads', icon: MapIcon, label: 'Network Topology' },
              { id: 'signals', icon: Zap, label: 'Signal Controllers' },
              { id: 'incidents', icon: AlertTriangle, label: 'Incident Events' },
              { id: 'vehicles', icon: Crosshair, label: 'Live Vehicles (geo)' }
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

        {/* Location Search Panel: set a feed's map-marker location by address */}
        {showLocPanel && (
          <div className="absolute top-20 left-6 z-30 bg-industrial-panel/90 border border-lcd-green/30 p-4 w-80 font-mono text-xs text-lcd-green flex flex-col gap-3 shadow-2xl backdrop-blur-md pointer-events-auto">
            <div className="flex justify-between items-center mb-1 pb-1 border-b border-lcd-green/20">
              <span className="font-bold uppercase tracking-widest">Set Feed Location</span>
              <X size={14} className="cursor-pointer hover:text-white" onClick={() => setShowLocPanel(false)} />
            </div>
            <select
              value={locFeedKey}
              onChange={e => { setLocFeedId(e.target.value); setLocStatus(null); }}
              className="bg-industrial-bg border border-lcd-green/30 px-2 py-1 text-[10px] uppercase tracking-widest outline-none"
            >
              {locFeeds.map(f => (
                <option key={f.feed_id} value={f.feed_id}>{f.config?.name || f.feed_id}</option>
              ))}
            </select>
            <div className="text-[10px] opacity-60">
              Current: {locFeed?.config?.latitude != null && locFeed?.config?.longitude != null
                ? `${locFeed.config.latitude.toFixed(5)}, ${locFeed.config.longitude.toFixed(5)}`
                : 'not set'}
            </div>
            <div className="flex items-center gap-2 border border-lcd-green/30 bg-industrial-bg px-2 py-1">
              <Search size={12} className="opacity-60 shrink-0" />
              <input
                value={locQuery}
                onChange={e => setLocQuery(e.target.value)}
                placeholder="Search address / place…"
                className="bg-transparent outline-none w-full text-[11px] placeholder:text-lcd-green/30"
              />
            </div>
            {locSearching && <div className="text-[10px] opacity-60 animate-pulse">searching…</div>}
            <div className="flex flex-col gap-1 max-h-48 overflow-y-auto">
              {locResults.map((r, i) => (
                <button
                  key={`${r.provider}-${r.lat}-${r.lon}-${i}`}
                  onClick={() => applyLocation(r)}
                  className="text-left px-2 py-1 border border-lcd-green/20 hover:bg-lcd-green hover:text-industrial-bg transition-all"
                  title={`${r.lat}, ${r.lon} (${r.provider})`}
                >
                  {r.label}
                </button>
              ))}
              {!locSearching && locQuery.trim().length >= 3 && locResults.length === 0 && (
                <div className="text-[10px] opacity-60 px-1">no results</div>
              )}
            </div>
            {locStatus && (
              <div className="text-[10px] text-[#facc15] border-t border-lcd-green/20 pt-2 break-all">{locStatus}</div>
            )}
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
            <ZoomOut size={18} />
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

        <DynamicTrafficMap ref={mapRef} activeLayer={activeLayer} activeLayers={activeLayers} showAllUnits={showAllUnits} calibrating={calibrating} />
      </div>
    </div>
    </AuthGuard>
  );
};

export default LiveMapPage;