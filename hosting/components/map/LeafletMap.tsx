'use client';

import React, { useEffect, useRef, useState, useImperativeHandle, forwardRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import { useRouter } from 'next/navigation';
import { cn } from '@/lib/utils';
import { Layers, Activity, AlertTriangle, Video, Map as MapIcon, ChevronRight, X, Zap, Loader2, Search, Bell } from 'lucide-react';
import { WebSocketMessageType } from '@/lib/websocket/WebSocketClient';
import { useWebSocket } from '@/lib/websocket/WebSocketProvider';
import { APIClient } from '@/lib/api/APIClient';
import { getApiBrowserBaseURL } from '@/lib/api/backendBaseUrl';
import { alertService } from '@/lib/services/alertService';
import { feedService, GeoCalibrationPoint } from '@/lib/services/feedService';

// Fix for default marker icons in Leaflet with Next.js
const fixLeafletIcons = () => {
  delete (L.Icon.Default.prototype as unknown as Record<string, unknown>)._getIconUrl;
  L.Icon.Default.mergeOptions({
    iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
    iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
    shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
  });
};

const SEVERITY_COLORS: Record<string, string> = {
  critical: '#ff3e3e',
  error: '#ff3e3e',
  warning: '#facc15',
  anomaly: '#fb923c',
  info: '#00ff41',
};

const sevColor = (s?: string) => SEVERITY_COLORS[(s || 'info').toLowerCase()] ?? '#00ff41';

const timeAgo = (ts: string | Date) => {
  const s = Math.max(0, (Date.now() - new Date(ts).getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  return `${Math.floor(s / 3600)}h ago`;
};

// Low average speed at a cam = congestion building. Green > 35, amber > 15, red below.
const speedColor = (kmh?: number | null) =>
  kmh == null ? '#00ff41' : kmh <= 15 ? '#ff3e3e' : kmh <= 35 ? '#facc15' : '#00ff41';

const LeafletMap = forwardRef(({ activeLayer, activeLayers, vehicles, vehiclesVersion, calibrating }: {
  activeLayer: 'satellite' | 'vector' | 'thermal';
  activeLayers: Record<string, boolean>;
  vehicles: React.RefObject<Record<string, any>>;
  vehiclesVersion: number;
  calibrating: boolean;
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
      // Fit the view to the known feed positions instead of a hardcoded point.
      const pts: [number, number][] = (feedsRef.current || [])
        .map(f => [f.config?.latitude || f.latitude, f.config?.longitude || f.longitude] as [number, number])
        .filter(([a, b]) => !!a && !!b);
      if (pts.length && leafletMap.current) {
        leafletMap.current.fitBounds(L.latLngBounds(pts), { padding: [60, 60] });
      } else {
        leafletMap.current?.setView([34.02, -118.02]);
      }
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
  const vehiclesLayer = useRef<L.LayerGroup>(L.layerGroup());
  const calibLayer = useRef<L.LayerGroup>(L.layerGroup());

  const tileLayerRef = useRef<L.TileLayer | null>(null);
  const feedMarkersRef = useRef<Record<string, L.Marker>>({});
  const feedRingsRef = useRef<Record<string, L.Circle>>({});
  const congestionMarkersRef = useRef<Record<string, L.Marker>>({});
  const segmentRef = useRef<Record<string, L.Polyline>>({});
  const incidentMarkersRef = useRef<Record<string, L.Marker>>({});
  const alertPosRef = useRef<Record<string, [number, number]>>({});
  const signalMarkersRef = useRef<Record<string, L.Marker>>({});
  // Live geo-projected vehicles (WS vehicles carry latitude/longitude once
  // the feed has geo_calibration; backend homography in geo_calibration.py).
  const vehicleMarkersRef = useRef<Record<string, L.CircleMarker>>({});
  const trailLinesRef = useRef<Record<string, L.Polyline>>({});
  const trailRef = useRef<Map<string, [number, number][]>>(new Map());
  // Calibration mode: map clicks collect WGS84 points; operator fills in the
  // matching normalized image coords (u, v) from the live video.
  const calibMarkersRef = useRef<L.Marker[]>([]);
  const [calibFeedId, setCalibFeedId] = useState<string>('');
  const [calibPoints, setCalibPoints] = useState<(GeoCalibrationPoint & { latlng: [number, number] })[]>([]);
  const [calibSaving, setCalibSaving] = useState(false);
  const [calibMsg, setCalibMsg] = useState<string>('');

  const { feeds, nodeCongestionData, alerts, subscribeToFeed } = useRealtimeUpdates();
  const router = useRouter();
  // Mirror of feeds for the imperative center() handle (fitBounds needs the
  // latest positions without re-creating the handle every render).
  const feedsRef = useRef<any[]>([]);
  feedsRef.current = feeds || [];

  const [selectedNode, setSelectedNode] = useState<any>(null);
  const [selectedAlert, setSelectedAlert] = useState<any>(null);
  const [alertAcking, setAlertAcking] = useState(false);
  const [showEventQueue, setShowEventQueue] = useState(true);
  const [signalStates, setSignalStates] = useState<Record<string, any>>({});
  const [isChangingPhase, setIsChangingPhase] = useState(false);
  const [isSettingCenter, setIsSettingCenter] = useState(false);

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

      centerMarker.on('dragstart', () => setIsSettingCenter(true));
      centerMarker.on('dragend', (e) => {
        const position = e.target.getLatLng();
        map.panTo(position);
        setIsSettingCenter(false);
      });

      feedsLayer.current.addTo(map);
      congestionLayer.current.addTo(map);
      incidentsLayer.current.addTo(map);
      roadNetworkLayer.current.addTo(map);
      signalsLayer.current.addTo(map);
      vehiclesLayer.current.addTo(map);
      calibLayer.current.addTo(map);

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

    const unsubscribe = wsClient?.subscribe(WebSocketMessageType.SIGNAL_UPDATE, (data: any) => {
      if (data?.signal_data) {
        setSignalStates(prev => ({
          ...prev,
          [data.signal_data.signal_id]: data.signal_data
        }));
      }
    });

    return () => {
      if (unsubscribe) unsubscribe();
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
    activeLayers.vehicles ? vehiclesLayer.current.addTo(leafletMap.current) : vehiclesLayer.current.remove();
    if (calibrating) calibLayer.current.addTo(leafletMap.current);
  }, [activeLayers, calibrating]);

  useEffect(() => {
    if (!leafletMap.current || !feeds) return;

    const liveFeedIds = new Set<string>();
    feeds.forEach(feed => {
      const lat = feed.config?.latitude || feed.latitude;
      const lng = feed.config?.longitude || feed.longitude;

      if (lat && lng) {
        liveFeedIds.add(feed.feed_id);
        const id = feed.feed_id;
        const statusColor = feed.status === 'running' ? '#00ff41' : feed.status === 'error' ? '#ff3e3e' : '#facc15';
        const m = feed.latest_metrics || {};
        const avgSpeed = m.average_speed_kmh ?? null;
        const liveCount = m.total_vehicles ?? 0;
        const ringColor = feed.status === 'running' ? speedColor(avgSpeed) : statusColor;

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

        const queueEntries = Object.entries(m.queue_lengths || {})
          .map(([lane, q]) => `${lane}: ${q}`)
          .join(' &nbsp;');
        const laneEntries = Object.entries(m.lane_occupancy || {})
          .map(([lane, occ]) => `${lane}: ${(Number(occ) * 100).toFixed(0)}%`)
          .join(' &nbsp;');
        const popupHtml = `
          <div style="font-family: monospace; color: #00ff41; background: #000; padding: 8px; min-width: 180px;">
            <div style="font-weight: bold; border-bottom: 1px solid #333; margin-bottom: 5px;">${feed.name || feed.feed_id}</div>
            <div style="font-size: 10px; opacity: 0.8;">${feed.source}</div>
            <div style="font-size: 10px; margin-top: 5px;">STATUS: <span style="color: ${statusColor}">${feed.status.toUpperCase()}</span></div>
            <div style="font-size: 10px; margin-top: 6px; border-top: 1px dashed #333; padding-top: 5px;">
              AVG SPEED: <b style="color: ${speedColor(avgSpeed)}">${avgSpeed != null ? avgSpeed.toFixed(1) + ' km/h' : 'N/A'}</b><br/>
              VEHICLES NOW: <b>${liveCount}</b> &nbsp; CUMULATIVE: <b>${m.total_vehicles_cumulative ?? 0}</b><br/>
              ${queueEntries ? `QUEUES: ${queueEntries}<br/>` : ''}
              ${laneEntries ? `LANE OCC: ${laneEntries}` : ''}
            </div>
            <button onclick="window.__openFeed && window.__openFeed('${id}')"
              style="margin-top: 6px; width: 100%; background: #00ff41; color: #000; border: 0; padding: 4px; font-family: monospace; font-weight: bold; font-size: 10px; cursor: pointer;">
              OPEN FEED &gt;
            </button>
          </div>
        `;

        if (feedMarkersRef.current[id]) {
          feedMarkersRef.current[id].setLatLng([lat, lng]);
          feedMarkersRef.current[id].setIcon(icon);
          feedMarkersRef.current[id].setPopupContent(popupHtml);
        } else {
          const marker = L.marker([lat, lng], { icon }).addTo(feedsLayer.current);
          marker.bindPopup(popupHtml);
          marker.on('click', () => setSelectedAlert(null));
          feedMarkersRef.current[id] = marker;
        }

        // Live telemetry ring around each cam: radius scales with the live
        // vehicle count, color encodes congestion via average speed.
        const radius = 40 + Math.min(Number(liveCount) || 0, 60) * 6;
        if (feedRingsRef.current[id]) {
          feedRingsRef.current[id].setLatLng([lat, lng]);
          feedRingsRef.current[id].setRadius(radius);
          feedRingsRef.current[id].setStyle({ color: ringColor, fillColor: ringColor });
        } else {
          feedRingsRef.current[id] = L.circle([lat, lng], {
            radius,
            color: ringColor,
            fillColor: ringColor,
            fillOpacity: 0.12,
            weight: 1,
            className: 'feed-ring',
          }).addTo(feedsLayer.current);
        }
      }
    });

    // Prune markers/rings for feeds that disappeared.
    for (const id of Object.keys(feedMarkersRef.current)) {
      if (!liveFeedIds.has(id)) {
        feedsLayer.current.removeLayer(feedMarkersRef.current[id]);
        delete feedMarkersRef.current[id];
      }
    }
    for (const id of Object.keys(feedRingsRef.current)) {
      if (!liveFeedIds.has(id)) {
        feedsLayer.current.removeLayer(feedRingsRef.current[id]);
        delete feedRingsRef.current[id];
      }
    }
  }, [feeds]);

  // Incident/alert markers (the "Incident Events" layer was a dead toggle —
  // incidentsLayer existed but nothing was ever added to it). Alerts land at
  // their own lat/lng when present, otherwise at the owning camera's position.
  useEffect(() => {
    if (!leafletMap.current || !alerts) return;

    const feedPos = new Map<string, [number, number]>();
    (feeds || []).forEach(f => {
      const lat = f.config?.latitude || f.latitude;
      const lng = f.config?.longitude || f.longitude;
      if (lat && lng) feedPos.set(f.feed_id, [lat, lng]);
    });

    const liveKeys = new Set<string>();
    alerts.forEach((alert, idx) => {
      const key = String(alert.id ?? `noid-${idx}`);
      let pos: [number, number] | undefined;
      if (alert.latitude && alert.longitude) {
        pos = [alert.latitude, alert.longitude];
      } else if (alert.feed_id && feedPos.has(alert.feed_id)) {
        pos = feedPos.get(alert.feed_id);
      }
      if (!pos) return;
      liveKeys.add(key);
      alertPosRef.current[key] = pos;

      const color = sevColor(alert.severity);
      const fresh = Date.now() - new Date(alert.timestamp).getTime() < 10 * 60 * 1000;
      const icon = L.divIcon({
        className: 'alert-marker',
        html: `<div class="${fresh ? 'alert-pulse' : ''}" style="
          width: 18px; height: 18px;
          transform: rotate(45deg);
          background: #000;
          border: 2px solid ${color};
          box-shadow: 0 0 10px ${color};
          display: flex; align-items: center; justify-content: center;
        "><span style="transform: rotate(-45deg); color: ${color}; font: bold 10px monospace;">!</span></div>`,
        iconSize: [18, 18],
        iconAnchor: [9, 9],
      });

      if (incidentMarkersRef.current[key]) {
        incidentMarkersRef.current[key].setLatLng(pos);
        incidentMarkersRef.current[key].setIcon(icon);
      } else {
        const marker = L.marker(pos, { icon }).addTo(incidentsLayer.current);
        marker.on('click', () => {
          setSelectedNode(null);
          setSelectedAlert(alert);
          leafletMap.current?.flyTo(pos, Math.max(leafletMap.current.getZoom(), 16), { duration: 0.8 });
        });
        incidentMarkersRef.current[key] = marker;
      }
    });

    for (const key of Object.keys(incidentMarkersRef.current)) {
      if (!liveKeys.has(key)) {
        incidentsLayer.current.removeLayer(incidentMarkersRef.current[key]);
        delete incidentMarkersRef.current[key];
        delete alertPosRef.current[key];
      }
    }
  }, [alerts, feeds]);

  // Feed popup "OPEN FEED" button calls this via inline onclick (Leaflet
  // popups are raw HTML, so no React handler binding is possible there).
  useEffect(() => {
    (window as any).__openFeed = (id: string) => {
      try { subscribeToFeed?.(id); } catch { /* non-fatal */ }
      router.push('/dashboard');
    };
    return () => { delete (window as any).__openFeed; };
  }, [router, subscribeToFeed]);

  const focusAlert = (alert: any, idx: number) => {
    const key = String(alert.id ?? `noid-${idx}`);
    const pos = alertPosRef.current[key];
    setSelectedNode(null);
    setSelectedAlert(alert);
    if (pos && leafletMap.current) {
      leafletMap.current.flyTo(pos, Math.max(leafletMap.current.getZoom(), 16), { duration: 0.8 });
    }
  };

  const handleAcknowledgeAlert = async (alert: any) => {
    if (typeof alert?.id !== 'number') return;
    setAlertAcking(true);
    try {
      await alertService.acknowledgeAlert(alert.id, true);
      setSelectedAlert({ ...alert, acknowledged: true });
    } finally {
      setAlertAcking(false);
    }
  };

  // --- Live geo vehicles layer -------------------------------------------------
  // vehicles arrive per frame via VIDEO_METADATA; when the feed is
  // geo-calibrated each carries latitude/longitude (backend homography).
  // Markers are cheap circleMarkers; trails are a capped client-side ring
  // buffer of the last N geo points (no backend persistence needed).
  useEffect(() => {
    if (!leafletMap.current) return;
    const all = Object.values(vehicles?.current || {});
    const liveKeys = new Set<string>();

    for (const v of all) {
      const lat = v.latitude, lng = v.longitude;
      if (typeof lat !== 'number' || typeof lng !== 'number') continue;
      const key = String(v.global_vehicle_id || v.vehicle_id);
      liveKeys.add(key);
      const pos: [number, number] = [lat, lng];

      const color = v.is_wrong_way ? '#ff3e3e' : (v.class_name === 'person' || v.class_name === 'bicycle') ? '#facc15' : '#00ff41';
      if (vehicleMarkersRef.current[key]) {
        vehicleMarkersRef.current[key].setLatLng(pos);
        vehicleMarkersRef.current[key].setStyle({ color, fillColor: color });
      } else {
        vehicleMarkersRef.current[key] = L.circleMarker(pos, {
          radius: v.is_wrong_way ? 6 : 4,
          color, fillColor: color, fillOpacity: 0.8, weight: 1,
        }).addTo(vehiclesLayer.current);
        vehicleMarkersRef.current[key].bindTooltip(
          `${v.class_name || 'vehicle'} ${v.license_plate ? '· ' + v.license_plate : ''}${v.is_wrong_way ? ' · WRONG WAY' : ''}`,
          { direction: 'top', offset: [0, -6], className: 'vehicle-tooltip' }
        );
      }

      let trail = trailRef.current.get(key);
      if (!trail) { trail = []; trailRef.current.set(key, trail); }
      const last = trail[trail.length - 1];
      if (!last || last[0] !== lat || last[1] !== lng) {
        trail.push(pos);
        if (trail.length > 25) trail.shift();
      }
      if (trailLinesRef.current[key]) {
        trailLinesRef.current[key].setLatLngs(trail);
      } else if (trail.length > 1) {
        trailLinesRef.current[key] = L.polyline(trail, {
          color, weight: 2, opacity: 0.55, dashArray: '4 4',
        }).addTo(vehiclesLayer.current);
      }
    }

    for (const key of Object.keys(vehicleMarkersRef.current)) {
      if (!liveKeys.has(key)) {
        vehiclesLayer.current.removeLayer(vehicleMarkersRef.current[key]);
        delete vehicleMarkersRef.current[key];
        const tl = trailLinesRef.current[key];
        if (tl) { vehiclesLayer.current.removeLayer(tl); delete trailLinesRef.current[key]; }
        trailRef.current.delete(key);
      }
    }
  }, [vehiclesVersion, vehicles]);

  // --- Calibration mode ----------------------------------------------------------
  // Prefill from the selected feed's persisted geo_calibration, and default the
  // feed selector to the first feed.
  useEffect(() => {
    if (!calibrating) return;
    const list = feeds || [];
    if (!calibFeedId && list.length) setCalibFeedId(list[0].feed_id);
    const f = list.find(x => x.feed_id === (calibFeedId || list[0]?.feed_id));
    const existing = f?.config?.geo_calibration;
    if (existing && existing.length) {
      setCalibPoints(existing.map(p => ({ ...p, latlng: [p.lat, p.lng] as [number, number] })));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [calibrating, calibFeedId, feeds]);

  // Map clicks collect WGS84 points while calibrating.
  useEffect(() => {
    const map = leafletMap.current;
    if (!map || !calibrating) return;
    const handler = (e: L.LeafletMouseEvent) => {
      const latlng: [number, number] = [e.latlng.lat, e.latlng.lng];
      setCalibPoints(pts => [...pts, { u: NaN, v: NaN, lat: latlng[0], lng: latlng[1], latlng }]);
    };
    map.on('click', handler);
    return () => { map.off('click', handler); };
  }, [calibrating, leafletMap.current]);

  // Draw numbered calibration markers.
  useEffect(() => {
    if (!leafletMap.current) return;
    calibMarkersRef.current.forEach(m => calibLayer.current.removeLayer(m));
    calibMarkersRef.current = calibPoints.map((p, i) => {
      const m = L.marker(p.latlng, {
        icon: L.divIcon({
          className: 'calib-marker',
          html: `<div style="width:20px;height:20px;border-radius:50%;background:#000;border:2px solid #facc15;color:#facc15;font:bold 10px monospace;display:flex;align-items:center;justify-content:center;box-shadow:0 0 8px #facc15;">${i + 1}</div>`,
          iconSize: [20, 20],
          iconAnchor: [10, 10],
        }),
      }).addTo(calibLayer.current);
      return m;
    });
  }, [calibPoints]);

  const handleSaveCalibration = async () => {
    if (!calibFeedId) { setCalibMsg('NO FEED SELECTED'); return; }
    const pts = calibPoints
      .filter(p => Number.isFinite(p.u) && Number.isFinite(p.v))
      .map(p => ({ u: p.u, v: p.v, lat: p.lat, lng: p.lng }));
    if (pts.length < 4) { setCalibMsg(`NEED >=4 POINTS WITH IMAGE COORDS (have ${pts.length})`); return; }
    setCalibSaving(true);
    setCalibMsg('UPLOADING…');
    try {
      await feedService.updateFeedConfig(calibFeedId, { geo_calibration: pts });
      setCalibMsg(`SAVED ${pts.length} PTS — LIVE UNITS NOW PROJECTED`);
    } catch (e: any) {
      setCalibMsg(`SAVE FAILED: ${e?.data?.detail || e?.message || 'error'}`);
    } finally {
      setCalibSaving(false);
    }
  };

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

  const handleSetPhase = async (signalId: string, phase: string) => {
    setIsChangingPhase(true);
    try {
      // REST, not WebSocket: the backend WebSocketMessageTypeEnum has no
      // set_signal_phase member, so the old WS send failed Pydantic validation
      // and was dropped silently (ws.py logs "Invalid message format"). The
      // REST endpoint carries the operator RBAC + per-signal scope check
      // (signals.py get_current_operator), which the WS path never had.
      // `duration_seconds` is not part of the REST contract -- send only phase.
      const apiClient = APIClient.getInstance({ baseURL: getApiBrowserBaseURL() });
      await apiClient.post(`/api/v1/signals/${signalId}/set_phase`, {
        phase: phase.toLowerCase(),
      });
    } catch (err: unknown) {
      const detail =
        (err as { data?: { detail?: string } })?.data?.detail ||
        (err as Error)?.message ||
        'Failed to set signal phase';
      console.error(`[LeafletMap] set_phase failed for ${signalId}:`, detail);
    } finally {
      setIsChangingPhase(false);
    }
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
    <div className={cn("relative w-full h-full bg-[#0a0a0a] overflow-hidden", (isSettingCenter || calibrating) && "cursor-crosshair")}>
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

      {/* Event Queue: live WS alerts, newest first. Click an entry to fly to
          its marker and open the incident panel. */}
      {alerts && alerts.length > 0 && (
        <div className="absolute bottom-4 left-4 z-[1002] w-72 font-mono bg-black/95 border border-[#00ff41]/40 shadow-[0_0_20px_rgba(0,255,65,0.15)]">
          <button
            onClick={() => setShowEventQueue(v => !v)}
            className="w-full flex items-center justify-between px-3 py-2 text-[10px] font-bold uppercase tracking-widest text-[#00ff41] border-b border-[#00ff41]/20 hover:bg-[#00ff41]/10"
          >
            <span className="flex items-center gap-2"><Bell size={12} /> Event Queue ({alerts.length})</span>
            <span>{showEventQueue ? '▾' : '▴'}</span>
          </button>
          {showEventQueue && (
            <div className="max-h-52 overflow-y-auto">
              {[...alerts].map((a, i) => ({ a, i }))
                .sort((x, y) => new Date(y.a.timestamp).getTime() - new Date(x.a.timestamp).getTime())
                .map(({ a, i }) => (
                  <button
                    key={String(a.id ?? i)}
                    onClick={() => focusAlert(a, i)}
                    className="w-full flex items-center gap-2 px-3 py-1.5 text-left text-[10px] text-[#00ff41]/90 hover:bg-[#00ff41]/10 border-b border-[#00ff41]/5 last:border-0"
                  >
                    <span
                      className="w-2 h-2 rotate-45 shrink-0 border"
                      style={{ borderColor: sevColor(a.severity), background: sevColor(a.severity) + '33' }}
                    />
                    <span className="truncate flex-1">{a.message || `ALERT #${a.id}`}</span>
                    <span className="opacity-50 shrink-0">{timeAgo(a.timestamp)}</span>
                  </button>
                ))}
            </div>
          )}
        </div>
      )}

      {/* Alert / Incident Detail Panel */}
      {selectedAlert && (
        <div
          className="absolute bottom-4 right-4 z-[1002] bg-black/95 border-2 p-4 w-72 font-mono shadow-[0_0_30px_rgba(0,255,65,0.2)]"
          style={{ borderColor: sevColor(selectedAlert.severity) }}
        >
          <div className="flex justify-between items-start mb-3">
            <div>
              <h3 className="text-sm font-bold uppercase tracking-tighter" style={{ color: sevColor(selectedAlert.severity) }}>
                Incident Uplink
              </h3>
              <p className="text-[10px] text-[#00ff41]/60">ALERT #{selectedAlert.id ?? '—'} · {timeAgo(selectedAlert.timestamp)}</p>
            </div>
            <X size={16} className="cursor-pointer text-[#00ff41]/60 hover:text-white" onClick={() => setSelectedAlert(null)} />
          </div>

          <div className="text-[11px] text-[#00ff41] mb-3 leading-snug">
            {selectedAlert.message || 'No description'}
          </div>

          <div className="grid grid-cols-2 gap-2 text-[10px] mb-3">
            <div className="bg-black border border-white/10 p-2">
              <p className="opacity-40 uppercase text-[8px]">Severity</p>
              <p className="font-bold uppercase" style={{ color: sevColor(selectedAlert.severity) }}>{selectedAlert.severity || 'INFO'}</p>
            </div>
            <div className="bg-black border border-white/10 p-2">
              <p className="opacity-40 uppercase text-[8px]">Status</p>
              <p className="font-bold uppercase text-[#00ff41]">{selectedAlert.status || 'OPEN'}</p>
            </div>
            <div className="bg-black border border-white/10 p-2 col-span-2">
              <p className="opacity-40 uppercase text-[8px]">Source Feed</p>
              <p className="font-bold truncate text-[#00ff41]">{selectedAlert.feed_id || 'N/A'}</p>
            </div>
          </div>

          <div className="flex gap-2">
            {typeof selectedAlert.id === 'number' && !selectedAlert.acknowledged && (
              <button
                disabled={alertAcking}
                onClick={() => handleAcknowledgeAlert(selectedAlert)}
                className="flex-1 text-[9px] py-1.5 border border-[#00ff41] text-[#00ff41] font-bold uppercase transition-all hover:scale-[1.02] active:scale-95 disabled:opacity-50"
              >
                {alertAcking ? 'ACKING…' : 'Acknowledge'}
              </button>
            )}
            {selectedAlert.acknowledged && (
              <span className="flex-1 text-[9px] py-1.5 border border-[#00ff41]/40 text-[#00ff41]/60 text-center uppercase">Acknowledged</span>
            )}
            <button
              onClick={() => router.push('/incidents')}
              className="flex-1 text-[9px] py-1.5 border border-[#facc15] text-[#facc15] font-bold uppercase transition-all hover:scale-[1.02] active:scale-95"
            >
              Incidents →
            </button>
          </div>
        </div>
      )}

      {/* Node & Signal Info Panel */}
      {selectedNode && !selectedAlert && (
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


      {/* Calibration Panel: click >=4 points on the map, then enter the matching
          normalized image coords (u, v in [0,1]) seen on that feed's live video.
          Saved via PATCH /api/v1/feeds/{id}/config; backend builds the homography. */}
      {calibrating && (
        <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-[1002] w-[26rem] font-mono bg-black/95 border border-[#facc15]/50 p-3 shadow-[0_0_25px_rgba(250,204,21,0.2)]">
          <div className="flex items-center justify-between mb-2">
            <span className="text-[10px] font-bold uppercase tracking-widest text-[#facc15]">Geo Calibration</span>
            <span className="text-[9px] text-[#facc15]/60 uppercase">click map → fill image u,v</span>
          </div>
          <select
            value={calibFeedId}
            onChange={e => setCalibFeedId(e.target.value)}
            className="w-full mb-2 bg-black border border-[#facc15]/40 text-[#facc15] text-[10px] p-1.5 uppercase"
          >
            {(feeds || []).map(f => (
              <option key={f.feed_id} value={f.feed_id}>{f.name || f.feed_id}</option>
            ))}
          </select>
          <div className="max-h-40 overflow-y-auto mb-2">
            {calibPoints.length === 0 && (
              <div className="text-[9px] text-[#facc15]/50 italic p-1">No points yet — click the map.</div>
            )}
            {calibPoints.map((p, i) => (
              <div key={i} className="flex items-center gap-1 py-0.5 text-[9px] text-[#00ff41]/80 border-b border-white/5">
                <span className="w-5 h-5 shrink-0 rounded-full border border-[#facc15] text-[#facc15] flex items-center justify-center font-bold">{i + 1}</span>
                <span className="shrink-0 opacity-70">{p.lat.toFixed(5)}, {p.lng.toFixed(5)}</span>
                <span className="opacity-50 shrink-0">u</span>
                <input
                  type="number" step="0.01" min="0" max="1" placeholder="0.xx"
                  value={Number.isFinite(p.u) ? p.u : ''}
                  onChange={e => setCalibPoints(pts => pts.map((q, j) => j === i ? { ...q, u: parseFloat(e.target.value) } : q))}
                  className="w-14 bg-black border border-[#00ff41]/30 text-[#00ff41] px-1 py-0.5"
                />
                <span className="opacity-50 shrink-0">v</span>
                <input
                  type="number" step="0.01" min="0" max="1" placeholder="0.xx"
                  value={Number.isFinite(p.v) ? p.v : ''}
                  onChange={e => setCalibPoints(pts => pts.map((q, j) => j === i ? { ...q, v: parseFloat(e.target.value) } : q))}
                  className="w-14 bg-black border border-[#00ff41]/30 text-[#00ff41] px-1 py-0.5"
                />
                <button
                  onClick={() => setCalibPoints(pts => pts.filter((_, j) => j !== i))}
                  className="ml-auto text-[#ff3e3e] hover:text-white px-1"
                  title="Remove point"
                >✕</button>
              </div>
            ))}
          </div>
          <div className="flex items-center gap-2">
            <button
              disabled={calibSaving}
              onClick={handleSaveCalibration}
              className="flex-1 text-[10px] py-1.5 border border-[#facc15] text-[#facc15] font-bold uppercase hover:bg-[#facc15] hover:text-black transition-all disabled:opacity-50"
            >
              {calibSaving ? 'Saving…' : `Save ${calibPoints.filter(p => Number.isFinite(p.u) && Number.isFinite(p.v)).length}/4+ pts`}
            </button>
            <button
              onClick={() => { setCalibPoints([]); setCalibMsg(''); }}
              className="text-[10px] py-1.5 px-2 border border-[#ff3e3e]/60 text-[#ff3e3e] uppercase font-bold hover:bg-[#ff3e3e] hover:text-black transition-all"
            >
              Clear
            </button>
          </div>
          {calibMsg && <div className="mt-1.5 text-[9px] uppercase text-[#00ff41]">{calibMsg}</div>}
        </div>
      )}

      {/* Matrix Overlay Effect */}
      <div className="absolute inset-0 pointer-events-none border-[1px] border-[#00ff41]/10 z-20 shadow-[inset_0_0_50px_rgba(0,255,65,0.05)]" />
    </div>
  );
});

export default LeafletMap;
LeafletMap.displayName = 'LeafletMap';