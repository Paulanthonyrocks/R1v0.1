'use client';

import React, { useEffect, useRef, useState, useMemo, useImperativeHandle, forwardRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import { useAuth } from '@/lib/auth/AuthProvider';
import { useRouter } from 'next/navigation';
import { cn } from '@/lib/utils';
import { Layers, Activity, AlertTriangle, Video, Map as MapIcon, ChevronRight, X, Zap, Loader2, Search, Bell, Navigation, Crosshair, Pencil, Trash2 } from 'lucide-react';
import { WebSocketMessageType } from '@/lib/websocket/WebSocketClient';
import { useWebSocket } from '@/lib/websocket/WebSocketProvider';
import { APIClient } from '@/lib/api/APIClient';
import { getApiBrowserBaseURL } from '@/lib/api/backendBaseUrl';
import { alertService } from '@/lib/services/alertService';
import { feedService, GeoCalibrationPoint } from '@/lib/services/feedService';
import { roadsService } from '@/lib/services/roadsService';
import { Road, RoadAssetsResponse } from '@/lib/types/api';
import { headingConeLatLngs, destinationPoint } from '@/lib/utils/geo';
import { pointAlongPolyline } from '@/lib/utils/coverage';
import { parseFeedQuery, matchesFeed, matchesNodeQuery } from '@/lib/utils/feedSearch';
import { allCoverageGaps, COVERAGE_RADIUS_M } from '@/lib/utils/coverage';
import { corridorCameras, CorridorCamera } from '@/lib/utils/corridor';

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
  // Registered-road polylines (the roads registry), drawn under the
  // "Network Topology" toggle alongside the congestion graph.
  const roadPolylinesLayer = useRef<L.LayerGroup>(L.layerGroup());
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

  // Camera placement mode: open from a feed popup's PLACE/ORIENT button, click
  // the map to drop the camera, snap to the nearest registered road, set the
  // facing heading, save via PATCH /api/v1/feeds/{id}/config. The backend
  // broadcasts the new config on the next WS feed_update (marker + cone move).
  const CONE_RADIUS_M = 150;
  const feedConesRef = useRef<Record<string, L.Polygon>>({});
  const placeLayer = useRef<L.LayerGroup>(L.layerGroup());
  const [placingFeedId, setPlacingFeedId] = useState<string>('');
  const [placement, setPlacement] = useState<{
    lat: number; lng: number; roadId: string; heading: number | ''; fov: number;
    mountHeight: number | ''; snapBearing: number | null; snapDist: number | null;
  } | null>(null);
  const [roads, setRoads] = useState<Road[]>([]);
  const roadHighlightRef = useRef<L.Polyline | null>(null);
  const [showCoverage, setShowCoverage] = useState(false);
  const coverageLayerRef = useRef<L.LayerGroup>(L.layerGroup());
  const [coverageSummary, setCoverageSummary] = useState('');
  const [showAssets, setShowAssets] = useState(false);
  const assetsLayerRef = useRef<L.LayerGroup>(L.layerGroup());
  const [assets, setAssets] = useState<RoadAssetsResponse | null>(null);
  const [selectedRoadId, setSelectedRoadId] = useState('');
  const [corridorPlaying, setCorridorPlaying] = useState(false);
  const [corridorIdx, setCorridorIdx] = useState(0);
  const [roadBusy, setRoadBusy] = useState(false);
  const [roadMsg, setRoadMsg] = useState('');

  // Road registry powers search filters (road:, dir:with-road) and the
  // placement panel dropdown. Gated on token: firing before auth resolves made
  // the request go out without Authorization -> 401, and the [] deps meant the
  // registry stayed empty for the whole session.
  const { token } = useAuth();
  useEffect(() => {
    if (!token) return;
    roadsService.listRoads().then(setRoads).catch(() => setRoads([]));
  }, [token]);
  const [placingSaving, setPlacingSaving] = useState(false);
  const [placingMsg, setPlacingMsg] = useState<string>('');
  // Register-road mini-form in the placement panel (dead-end case: no road
  // within snap range of the clicked point).
  const [newRoadName, setNewRoadName] = useState('');
  const [registeringRoad, setRegisteringRoad] = useState(false);

  const { feeds, nodeCongestionData, alerts, subscribeToFeed } = useRealtimeUpdates();

  const corridor: CorridorCamera[] = useMemo(() => {
    const road = roads.find(r => r.id === selectedRoadId);
    return road ? corridorCameras(road, feeds || []) : [];
  }, [roads, feeds, selectedRoadId]);

  // Drive-through: while playing, fly to each corridor camera in chainage order.
  useEffect(() => {
    if (!corridorPlaying || corridor.length === 0 || !leafletMap.current) return;
    const cam = corridor[corridorIdx % corridor.length];
    const lat = cam.feed.config?.latitude ?? cam.feed.latitude;
    const lng = cam.feed.config?.longitude ?? cam.feed.longitude;
    if (lat && lng) leafletMap.current.flyTo([lat, lng], 18, { duration: 1.2 });
    const t = setTimeout(() => setCorridorIdx(i => (i + 1) % corridor.length), 2500);
    return () => clearTimeout(t);
  }, [corridorPlaying, corridorIdx, corridor]);

  // Road registry admin from the corridor panel: rename (PATCH) and delete
  // (DELETE, after checking assigned feeds via GET /roads/{id}/feeds).
  const handleRenameRoad = async () => {
    const road = roads.find(r => r.id === selectedRoadId);
    if (!road || roadBusy) return;
    const name = window.prompt('Rename road', road.name);
    if (!name || !name.trim() || name.trim() === road.name) return;
    setRoadBusy(true);
    setRoadMsg('');
    try {
      const updated = await roadsService.updateRoad(road.id, { name: name.trim() });
      setRoads(prev => prev.map(r => (r.id === road.id ? updated : r)));
      setRoadMsg(`RENAMED → ${updated.name.toUpperCase()}`);
    } catch (e) {
      setRoadMsg(`RENAME FAILED: ${(e as Error)?.message || e}`);
    } finally {
      setRoadBusy(false);
    }
  };

  const handleDeleteRoad = async () => {
    const road = roads.find(r => r.id === selectedRoadId);
    if (!road || roadBusy) return;
    let assigned = 0;
    try { assigned = (await roadsService.feedsOnRoad(road.id)).length; } catch { /* count is advisory only */ }
    const ok = window.confirm(
      `Delete road '${road.name}' (${road.id})?` +
      (assigned ? `\n${assigned} feed(s) are assigned to it — their road_id stays set but the road will be gone from the registry.` : ''),
    );
    if (!ok) return;
    setRoadBusy(true);
    setRoadMsg('');
    try {
      await roadsService.deleteRoad(road.id);
      setRoads(prev => prev.filter(r => r.id !== road.id));
      setSelectedRoadId('');
      setCorridorPlaying(false);
    } catch (e) {
      setRoadMsg(`DELETE FAILED: ${(e as Error)?.message || e}`);
    } finally {
      setRoadBusy(false);
    }
  };

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
      placeLayer.current.addTo(map);

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

  // Registered-road polylines: redraw whenever the registry changes (e.g. a
  // road registered from the placement panel) and honour the Network
  // Topology toggle.
  useEffect(() => {
    const map = leafletMap.current;
    if (!map) return;
    roadPolylinesLayer.current.clearLayers();
    for (const r of roads) {
      if (!r.polyline || r.polyline.length < 2) continue;
      L.polyline(r.polyline.map(p => [p.lat, p.lng] as [number, number]), {
        color: '#22d3ee', weight: 3, opacity: 0.8, dashArray: '6 4',
      })
        .bindTooltip(
          `${r.name} (${r.id})${r.one_way_bearing_deg != null ? ` — one-way ${Math.round(r.one_way_bearing_deg)}°` : ''}`,
          { sticky: true },
        )
        .addTo(roadPolylinesLayer.current);
    }
    if (activeLayers.roads) roadPolylinesLayer.current.addTo(map);
    else roadPolylinesLayer.current.remove();
  }, [roads, activeLayers.roads]);

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
            ${feed.config?.road_id ? `<div style="font-size: 10px; margin-top: 3px;">ROAD: <b>${feed.config.road_id}</b></div>` : ''}
            ${feed.config?.heading_deg != null ? `<div style="font-size: 10px; margin-top: 3px;">FACING: <b>${Math.round(feed.config.heading_deg)}&deg;</b> <span style="opacity:0.6">${feed.config.heading_source === 'derived' ? '(derived)' : '(manual)'}</span></div>` : ''}
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
            <button onclick="window.__placeFeed && window.__placeFeed('${id}')"
              style="margin-top: 4px; width: 100%; background: transparent; color: #22d3ee; border: 1px solid #22d3ee; padding: 4px; font-family: monospace; font-weight: bold; font-size: 10px; cursor: pointer;">
              PLACE / ORIENT &gt;
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

  // --- Coverage gaps -------------------------------------------------------------
  // Toggle: paint every uncovered stretch of every registered road red.
  // Coverage = point lies inside some feed's cone (heading/fov/radius).
  useEffect(() => {
    const map = leafletMap.current;
    if (!map) return;
    coverageLayerRef.current.clearLayers();
    if (!showCoverage) {
      if (map.hasLayer(coverageLayerRef.current)) map.removeLayer(coverageLayerRef.current);
      setCoverageSummary('');
      return;
    }
    const gaps = allCoverageGaps(roads, feeds || [], { radiusM: CONE_RADIUS_M });
    let gapM = 0;
    for (const g of gaps) {
      gapM += g.lengthM;
      L.polyline([g.from, g.to], {
        color: '#ff3e3e', weight: 6, opacity: 0.9,
      })
        .bindTooltip(`${g.roadName}: ${Math.round(g.lengthM)} m UNCOVERED`, { sticky: true })
        .addTo(coverageLayerRef.current);
    }
    coverageLayerRef.current.addTo(map);
    const totalM = gaps.length >= 0
      ? roads.reduce((s, r) => {
          let len = 0;
          for (let i = 0; i < r.polyline.length - 1; i++) {
            const a = r.polyline[i];
            const b = r.polyline[i + 1];
            len += Math.hypot((b.lat - a.lat) * 111320, (b.lng - a.lng) * 111320 * Math.cos(a.lat * Math.PI / 180));
          }
          return s + len;
        }, 0)
      : 0;
    setCoverageSummary(
      gaps.length === 0
        ? 'ALL ROADS COVERED'
        : `${gaps.length} GAP SEGMENTS — ${Math.round(gapM)} m OF ${Math.round(totalM)} m UNCOVERED`,
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showCoverage, roads, feeds]);

  // --- Signals + work-zones on the road graph ------------------------------------
  // Poll GET /api/v1/roads/assets every 15 s (signal phases change); render
  // phase-colored markers at signals and orange segments for work-zones.
  useEffect(() => {
    let cancelled = false;
    const load = () => {
      // Hidden tab: skip the poll, it only burns rate-limit budget.
      if (document.hidden) return;
      roadsService.getAssets().then(a => { if (!cancelled) setAssets(a); }).catch(() => {});
    };
    if (token) load();
    const t = setInterval(load, 15000);
    return () => { cancelled = true; clearInterval(t); };
  }, [token]);

  useEffect(() => {
    const map = leafletMap.current;
    if (!map) return;
    assetsLayerRef.current.clearLayers();
    if (!showAssets || !assets) {
      if (map.hasLayer(assetsLayerRef.current)) map.removeLayer(assetsLayerRef.current);
      return;
    }
    const PHASE_COLOR: Record<string, string> = {
      GREEN: '#00ff41', YELLOW: '#facc15', RED: '#ff3e3e', RED_ALL: '#ff3e3e',
    };
    for (const [roadId, roadAssets] of Object.entries(assets.roads || {})) {
      const road = roads.find(r => r.id === roadId);
      const poly = road?.polyline.map(p => [p.lat, p.lng] as [number, number]);
      for (const s of roadAssets.signals || []) {
        const color = PHASE_COLOR[(s.phase || '').toUpperCase()] || '#9ca3af';
        L.circleMarker([s.latitude, s.longitude], {
          radius: 7, color, fillColor: color, fillOpacity: 0.9, weight: 2,
        })
          .bindTooltip(
            `${s.signal_id}: ${(s.phase || '?').toUpperCase()} @ ${roadAssets.road_name}` +
            (s.chainage_m != null ? ` ${Math.round(s.chainage_m)} m` : '') +
            (s.intersection_id ? ` · node ${s.intersection_id}` : ''),
            { sticky: true },
          )
          .addTo(assetsLayerRef.current);
      }
      for (const wz of roadAssets.work_zones || []) {
        if (!poly || wz.chainage_m == null) continue;
        // Work-zones are feed/lane-scoped; draw a +/-150 m segment around the
        // zone's chainage on the road.
        const total = poly.reduce((acc, p, i) => {
          if (i === 0) return 0;
          const a = poly[i - 1];
          return acc + Math.hypot((p[0] - a[0]) * 111320, (p[1] - a[1]) * 111320 * Math.cos(a[0] * Math.PI / 180));
        }, 0);
        const from = pointAlongPolyline(poly, Math.max(0, wz.chainage_m - 150));
        const to = pointAlongPolyline(poly, Math.min(total, wz.chainage_m + 150));
        if (from && to) {
          L.polyline([from, to], { color: '#fb923c', weight: 8, opacity: 0.85, dashArray: '10 6' })
            .bindTooltip(
              `WORK ZONE ${wz.id || ''} ${roadAssets.road_name} @ ${Math.round(wz.chainage_m)} m` +
              (wz.speed_limit != null ? ` · limit ${wz.speed_limit}` : '') +
              (wz.feed_id ? ` · via ${wz.feed_id}` : ''),
              { sticky: true },
            )
            .addTo(assetsLayerRef.current);
        }
      }
    }
    assetsLayerRef.current.addTo(map);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showAssets, assets, roads]);

  // --- Camera heading cones -----------------------------------------------------
  // Each feed with a heading (manual or derived from geo_calibration) gets a
  // coverage wedge on the map: apex at the camera, facing heading_deg, spread
  // fov_deg. The wedge appears/updates automatically on the next WS
  // feed_update once the backend has derived or received a heading.
  useEffect(() => {
    if (!leafletMap.current || !feeds) return;
    const live = new Set<string>();
    for (const feed of feeds) {
      const lat = feed.config?.latitude || feed.latitude;
      const lng = feed.config?.longitude || feed.longitude;
      const heading = feed.config?.heading_deg;
      const id = feed.feed_id;
      if (!lat || !lng || heading == null) continue;
      live.add(id);
      const fov = feed.config?.fov_deg ?? 60;
      const pts = headingConeLatLngs(lat, lng, heading, fov, CONE_RADIUS_M);
      const color = feed.status === 'running' ? '#00ff41' : feed.status === 'error' ? '#ff3e3e' : '#facc15';
      if (feedConesRef.current[id]) {
        feedConesRef.current[id].setLatLngs(pts);
        feedConesRef.current[id].setStyle({ color, fillColor: color });
      } else {
        feedConesRef.current[id] = L.polygon(pts, {
          color, fillColor: color, fillOpacity: 0.10, weight: 1, dashArray: '4 4',
          className: 'feed-cone',
        }).addTo(feedsLayer.current);
      }
    }
    for (const id of Object.keys(feedConesRef.current)) {
      if (!live.has(id)) {
        feedsLayer.current.removeLayer(feedConesRef.current[id]);
        delete feedConesRef.current[id];
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  // --- Camera placement mode ------------------------------------------------------
  // Feed popup "PLACE / ORIENT" calls this via inline onclick.
  useEffect(() => {
    (window as any).__placeFeed = (id: string) => {
      setPlacingFeedId(id);
      setPlacement(null);
      setPlacingMsg('CLICK THE MAP TO DROP THIS CAMERA');
      roadsService.listRoads()
        .then(setRoads)
        .catch(() => { setRoads([]); });
    };
    return () => { delete (window as any).__placeFeed; };
  }, []);

  // While placing, a map click drops the camera there and snaps to the nearest
  // registered road (POST /api/v1/roads/snap) to prefill road + road bearing.
  useEffect(() => {
    const map = leafletMap.current;
    if (!map || !placingFeedId) return;
    const handler = async (e: L.LeafletMouseEvent) => {
      const lat = e.latlng.lat;
      const lng = e.latlng.lng;
      const feed = feedsRef.current.find(f => f.feed_id === placingFeedId);
      let snap = null;
      try { snap = await roadsService.snap(lat, lng, 150); } catch { snap = null; }
      const prevHeading = feed?.config?.heading_deg;
      setPlacement({
        lat,
        lng,
        roadId: snap?.found ? (snap.road_id ?? '') : (feed?.config?.road_id ?? ''),
        heading: typeof prevHeading === 'number' ? prevHeading : (snap?.segment_bearing_deg ?? ''),
        fov: feed?.config?.fov_deg ?? 60,
        mountHeight: typeof feed?.config?.mount_height_m === 'number' ? feed.config.mount_height_m : '',
        snapBearing: snap?.segment_bearing_deg ?? null,
        snapDist: snap?.distance_m ?? null,
      });
      setPlacingMsg(
        snap?.found
          ? `SNAPPED TO ${snap.road_name} — ${snap.distance_m} m OFF LINE, ROAD BEARING ${snap.segment_bearing_deg}\u00B0`
          : 'NO REGISTERED ROAD WITHIN 150 m — REGISTER ONE BELOW',
      );
    };
    map.on('click', handler);
    return () => { map.off('click', handler); };
  }, [placingFeedId, leafletMap.current]);

  // Live preview: cone + marker at the draft position while the panel is open.
  useEffect(() => {
    if (!leafletMap.current) return;
    placeLayer.current.clearLayers();
    if (placingFeedId && placement && typeof placement.heading === 'number' && Number.isFinite(placement.heading)) {
      const pts = headingConeLatLngs(placement.lat, placement.lng, placement.heading, placement.fov, CONE_RADIUS_M);
      L.polygon(pts, {
        color: '#22d3ee', fillColor: '#22d3ee', fillOpacity: 0.15, weight: 1,
      }).addTo(placeLayer.current);
      L.circleMarker([placement.lat, placement.lng], {
        radius: 6, color: '#22d3ee', fillColor: '#22d3ee', fillOpacity: 0.9, weight: 1,
      }).addTo(placeLayer.current);
    } else if (placingFeedId && placement) {
      L.circleMarker([placement.lat, placement.lng], {
        radius: 6, color: '#22d3ee', fillColor: '#22d3ee', fillOpacity: 0.9, weight: 1,
      }).addTo(placeLayer.current);
    }
  }, [placement, placingFeedId]);

  const handleSavePlacement = async () => {
    if (!placingFeedId || !placement) return;
    setPlacingSaving(true);
    setPlacingMsg('');
    try {
      const updates: Record<string, unknown> = {
        latitude: placement.lat,
        longitude: placement.lng,
        road_id: placement.roadId || null,
        fov_deg: placement.fov,
      };
      // Sending a heading marks it manual server-side (heading_source); the
      // derived-from-geo_calibration value is used when this is omitted.
      if (typeof placement.heading === 'number' && Number.isFinite(placement.heading)) {
        updates.heading_deg = ((placement.heading % 360) + 360) % 360;
      }
      if (typeof placement.mountHeight === 'number' && Number.isFinite(placement.mountHeight)) {
        updates.mount_height_m = placement.mountHeight;
      }
      await feedService.updateFeedConfig(placingFeedId, updates);
      setPlacingMsg('SAVED — NEXT WS FEED_UPDATE MOVES MARKER + CONE');
    } catch (err) {
      setPlacingMsg(`SAVE FAILED: ${(err as Error)?.message || err}`);
    } finally {
      setPlacingSaving(false);
    }
  };

  // Register a new road through the clicked placement point: a 500 m polyline
  // centered on the camera, running along the current facing heading (falls
  // back to the snapped road bearing, then north). The feed is auto-assigned
  // to the new road so Save placement wires road_id in one flow.
  const handleRegisterRoad = async () => {
    if (!placement) return;
    const heading =
      typeof placement.heading === 'number' && Number.isFinite(placement.heading)
        ? ((placement.heading % 360) + 360) % 360
        : (placement.snapBearing ?? 0);
    const half = 250; // m each way -> 500 m road through the camera point
    const back = destinationPoint(placement.lat, placement.lng, (heading + 180) % 360, half);
    const fwd = destinationPoint(placement.lat, placement.lng, heading, half);
    const name = newRoadName.trim() || `Road ${roads.length + 1}`;
    setRegisteringRoad(true);
    setPlacingMsg('');
    try {
      const road = await roadsService.createRoad({
        name,
        polyline: [
          { lat: back[0], lng: back[1] },
          { lat: placement.lat, lng: placement.lng },
          { lat: fwd[0], lng: fwd[1] },
        ],
        one_way_bearing_deg: heading,
      });
      setRoads(prev => [...prev, road]);
      setPlacement(p => p && { ...p, roadId: road.id, snapBearing: heading, snapDist: 0 });
      setNewRoadName('');
      setPlacingMsg(`ROAD '${road.name}' REGISTERED THROUGH THIS POINT (${Math.round(heading)}°) — FEED ASSIGNED`);
    } catch (err) {
      setPlacingMsg(`REGISTER FAILED: ${(err as Error)?.message || err}`);
    } finally {
      setRegisteringRoad(false);
    }
  };

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

    // Placement-aware query language: free text + road:/dir:/status: tokens.
    const q = parseFeedQuery(query);
    const results: SearchResult[] = [];

    if (feeds) {
      feeds.forEach(feed => {
        if (matchesFeed(feed, q, roads)) {
          results.push({ type: 'feed', data: feed, label: feed.name || feed.feed_id });
        }
      });
    }

    if (nodeCongestionData) {
      nodeCongestionData.forEach(node => {
        if (matchesNodeQuery(node, q, roads)) {
          results.push({ type: 'node', data: node, label: `NODE: ${node.id}` });
        }
      });
    }

    setSearchResults(results.slice(0, 10));
  };

  const selectResult = (result: any) => {
    setSearchQuery(result.label);
    setSearchResults([]);

    // Highlight the assigned road when a feed result is selected.
    if (roadHighlightRef.current && leafletMap.current) {
      leafletMap.current.removeLayer(roadHighlightRef.current);
      roadHighlightRef.current = null;
    }
    if (result.type === 'feed') {
      const roadId = result.data.config?.road_id;
      const road = roadId ? roads.find(r => r.id === roadId) : undefined;
      const lat = result.data.config?.latitude || result.data.latitude;
      const lng = result.data.config?.longitude || result.data.longitude;
      if (road && leafletMap.current) {
        roadHighlightRef.current = L.polyline(
          road.polyline.map(p => [p.lat, p.lng] as [number, number]),
          { color: '#22d3ee', weight: 5, opacity: 0.8, dashArray: '8 6' },
        ).addTo(leafletMap.current);
      }
      if (lat && lng && leafletMap.current) {
        leafletMap.current.flyTo([lat, lng], 18, { duration: 1.5 });
      }
    } else if (result.type === 'node') {
      const lat = result.data.latitude;
      const lng = result.data.longitude;
      setSelectedNode(result.data);
      if (lat && lng && leafletMap.current) {
        leafletMap.current.flyTo([lat, lng], 18, { duration: 1.5 });
      }
    }
  };

  return (
    <div className={cn("relative w-full h-full bg-[#0a0a0a] overflow-hidden", (isSettingCenter || calibrating || placingFeedId) && "cursor-crosshair")}>
      <div ref={mapRef} className="absolute inset-0 z-0" />

      {/* Search Bar */}
      <div className="absolute top-20 left-4 z-[1002] w-72 font-mono">
        <div className="flex gap-1 mb-1">
          <button
            onClick={() => setShowCoverage(v => !v)}
            className={cn(
              'text-[9px] px-2 py-1 border font-bold uppercase tracking-wider transition-all',
              showCoverage
                ? 'bg-[#ff3e3e] text-black border-[#ff3e3e]'
                : 'bg-black/80 text-[#ff3e3e] border-[#ff3e3e]/50 hover:bg-[#ff3e3e]/20',
            )}
          >
            {showCoverage ? 'GAPS: ON' : 'GAPS: OFF'}
          </button>
          <button
            onClick={() => setShowAssets(v => !v)}
            className={cn(
              'text-[9px] px-2 py-1 border font-bold uppercase tracking-wider transition-all',
              showAssets
                ? 'bg-[#facc15] text-black border-[#facc15]'
                : 'bg-black/80 text-[#facc15] border-[#facc15]/50 hover:bg-[#facc15]/20',
            )}
            title="Traffic signals (phase-colored) and active work-zones on registered roads"
          >
            {showAssets ? 'ASSETS: ON' : 'ASSETS: OFF'}
          </button>
          {coverageSummary && (
            <span className="text-[9px] text-[#ff3e3e] self-center truncate">{coverageSummary}</span>
          )}
          <select
            value={selectedRoadId}
            onChange={(e) => { setSelectedRoadId(e.target.value); setCorridorPlaying(false); setCorridorIdx(0); }}
            className="ml-auto text-[9px] bg-black/80 border border-[#22d3ee]/50 text-[#22d3ee] uppercase font-bold px-1 py-1 max-w-[7rem]"
            title="Corridor drive-through: cameras on this road in chainage order"
          >
            <option value="">CORRIDOR…</option>
            {roads.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        </div>
        <div className="relative group">
          <div className="absolute inset-y-0 left-0 flex items-center pl-3 pointer-events-none">
            <Search className="w-4 h-4 text-[#00ff41]/50 group-focus-within:text-[#00ff41]" />
          </div>
          <input
            type="text"
            className="block w-full p-2 pl-10 text-xs text-[#00ff41] bg-black/80 border border-[#00ff41]/30 focus:ring-1 focus:ring-[#00ff41] focus:border-[#00ff41] placeholder-[#00ff41]/30 backdrop-blur-sm"
            placeholder='QUERY FEED OR NODE...  e.g. road:"Main St" dir:NE status:running'
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
                <div className="flex items-center gap-2 min-w-0">
                  {result.type === 'feed' ? <Video size={12} /> : <Activity size={12} />}
                  <span className="truncate">{result.label}</span>
                </div>
                {result.type === 'feed' && (
                  <div className="flex items-center gap-1.5 shrink-0 text-[9px] text-[#22d3ee]">
                    {result.data.config?.road_id && <span className="border border-[#22d3ee]/40 px-1 uppercase">{result.data.config.road_id}</span>}
                    {result.data.config?.heading_deg != null && (
                      <span className="border border-[#22d3ee]/40 px-1 flex items-center gap-0.5">
                        <Navigation size={8} style={{ transform: `rotate(${result.data.config.heading_deg}deg)` }} />
                        {Math.round(result.data.config.heading_deg)}°
                      </span>
                    )}
                  </div>
                )}
                <ChevronRight size={10} className="opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
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

      {/* Corridor drive-through panel: cameras on the selected road in chainage order */}
      {selectedRoadId && (
        <div className="absolute bottom-4 left-4 z-[1002] w-64 font-mono bg-black/95 border border-[#22d3ee]/50 p-2 shadow-[0_0_25px_rgba(34,211,238,0.2)]">
          <div className="flex items-center justify-between mb-1.5">
            <span className="text-[10px] font-bold uppercase tracking-widest text-[#22d3ee]">
              Corridor: {roads.find(r => r.id === selectedRoadId)?.name || selectedRoadId}
            </span>
            <div className="flex gap-1">
              <button
                onClick={() => { setCorridorPlaying(p => !p); setCorridorIdx(0); }}
                className="text-[9px] px-1.5 py-0.5 border border-[#00ff41]/60 text-[#00ff41] font-bold uppercase hover:bg-[#00ff41] hover:text-black transition-all"
              >
                {corridorPlaying ? 'Stop' : '▶ Drive'}
              </button>
              <button
                onClick={handleRenameRoad}
                disabled={roadBusy}
                title="Rename road (PATCH /api/v1/roads/{id})"
                className="text-[9px] px-1.5 py-0.5 border border-[#facc15]/60 text-[#facc15] font-bold uppercase hover:bg-[#facc15] hover:text-black transition-all disabled:opacity-40"
              >
                <Pencil size={10} />
              </button>
              <button
                onClick={handleDeleteRoad}
                disabled={roadBusy}
                title="Delete road (DELETE /api/v1/roads/{id}) — checks assigned feeds first"
                className="text-[9px] px-1.5 py-0.5 border border-[#ff3e3e]/60 text-[#ff3e3e] font-bold uppercase hover:bg-[#ff3e3e] hover:text-black transition-all disabled:opacity-40"
              >
                <Trash2 size={10} />
              </button>
              <button
                onClick={() => { setSelectedRoadId(''); setCorridorPlaying(false); setRoadMsg(''); }}
                className="text-[9px] px-1.5 py-0.5 border border-[#ff3e3e]/60 text-[#ff3e3e] font-bold uppercase hover:bg-[#ff3e3e] hover:text-black transition-all"
              >
                ✕
              </button>
            </div>
          </div>
          {roadMsg && <div className="text-[9px] uppercase text-[#facc15] mb-1">{roadMsg}</div>}
          {corridor.length === 0 ? (
            <div className="text-[9px] text-[#22d3ee]/50 italic p-1">No cameras on this road — use search (road:…) or PLACE/ORIENT to assign one.</div>
          ) : (
            <div className="max-h-44 overflow-y-auto">
              {corridor.map((c, i) => {
                const lat = c.feed.config?.latitude ?? c.feed.latitude;
                const lng = c.feed.config?.longitude ?? c.feed.longitude;
                const active = corridorPlaying && i === corridorIdx % corridor.length;
                return (
                  <button
                    key={c.feed.feed_id}
                    onClick={() => {
                      setCorridorIdx(i);
                      setCorridorPlaying(false);
                      if (lat && lng && leafletMap.current) leafletMap.current.flyTo([lat, lng], 18, { duration: 1.2 });
                    }}
                    className={cn(
                      'w-full flex items-center gap-2 text-left px-1.5 py-1 text-[10px] border-b border-[#22d3ee]/10 last:border-0 hover:bg-[#22d3ee]/10',
                      active && 'bg-[#22d3ee]/20',
                    )}
                  >
                    <span className="text-[#22d3ee]/60 w-12 shrink-0">{Math.round(c.distanceAlongM)} m</span>
                    <span className="truncate flex-1 text-[#00ff41]">{c.feed.name || c.feed.feed_id}</span>
                    <span className="text-[#22d3ee] shrink-0" title={`road bearing ${Math.round(c.segmentBearingDeg)}°`}>
                      {c.relation === 'with' ? '→' : c.relation === 'against' ? '←' : '·'}
                      {c.headingDeg != null ? ` ${Math.round(c.headingDeg)}°` : ''}
                    </span>
                    <span className={cn('shrink-0', c.feed.status === 'running' ? 'text-[#00ff41]' : 'text-[#ff3e3e]')}>
                      {c.feed.status === 'running' ? '●' : '○'}
                    </span>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      )}

      {/* Camera Placement Panel: opened from a feed popup's PLACE/ORIENT button.
          Click the map to drop the camera (snaps to nearest road), pick road +
          facing heading, save via PATCH /api/v1/feeds/{id}/config. */}
      {placingFeedId && (
        <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-[1002] w-[26rem] font-mono bg-black/95 border border-[#22d3ee]/50 p-3 shadow-[0_0_25px_rgba(34,211,238,0.2)]">
          <div className="flex items-center justify-between mb-2">
            <span className="text-[10px] font-bold uppercase tracking-widest text-[#22d3ee] flex items-center gap-1">
              <Crosshair size={12} /> Camera Placement
            </span>
            <span className="text-[9px] text-[#22d3ee]/60 uppercase">
              {(feeds || []).find(f => f.feed_id === placingFeedId)?.name || placingFeedId}
            </span>
          </div>

          {!placement && (
            <div className="text-[9px] text-[#22d3ee]/60 italic p-1 mb-2">Click the map where this camera stands…</div>
          )}

          {placement && (
            <div className="space-y-2 mb-2">
              <div className="text-[9px] text-[#00ff41]/70">
                POS: {placement.lat.toFixed(5)}, {placement.lng.toFixed(5)}
                {placement.snapDist != null && <> &nbsp;·&nbsp; {placement.snapDist} m off road line</>}
              </div>
              <label className="block text-[9px] uppercase text-[#22d3ee]/60">Road / corridor</label>
              <select
                value={placement.roadId}
                onChange={e => setPlacement(p => p && { ...p, roadId: e.target.value })}
                className="w-full bg-black border border-[#22d3ee]/40 text-[#22d3ee] text-[10px] p-1.5 uppercase"
              >
                <option value="">(none)</option>
                {roads.map(r => <option key={r.id} value={r.id}>{r.name}</option>)}
                {placement.roadId && !roads.some(r => r.id === placement.roadId) && (
                  <option value={placement.roadId}>{placement.roadId} (current)</option>
                )}
              </select>
              <div className="flex items-end gap-2">
                <div className="flex-1">
                  <label className="block text-[9px] uppercase text-[#22d3ee]/60">Facing heading (deg, 0=N)</label>
                  <input
                    type="number" min="0" max="360" step="1"
                    value={placement.heading}
                    onChange={e => setPlacement(p => p && { ...p, heading: e.target.value === '' ? '' : parseFloat(e.target.value) })}
                    className="w-full bg-black border border-[#22d3ee]/40 text-[#22d3ee] text-[10px] px-1.5 py-1"
                  />
                </div>
                <button
                  disabled={placement.snapBearing == null}
                  onClick={() => setPlacement(p => p && { ...p, heading: p.snapBearing ?? p.heading })}
                  className="text-[9px] py-1 px-2 border border-[#22d3ee]/60 text-[#22d3ee] uppercase font-bold hover:bg-[#22d3ee] hover:text-black transition-all disabled:opacity-40"
                  title="Use the snapped road segment's direction of travel"
                >
                  <Navigation size={10} className="inline mr-1" />
                  {placement.snapBearing != null ? `${Math.round(placement.snapBearing)}° road` : 'no road'}
                </button>
                <div className="w-20">
                  <label className="block text-[9px] uppercase text-[#22d3ee]/60">FOV°</label>
                  <input
                    type="number" min="5" max="360" step="5"
                    value={placement.fov}
                    onChange={e => setPlacement(p => p && { ...p, fov: parseFloat(e.target.value) || 60 })}
                    className="w-full bg-black border border-[#22d3ee]/40 text-[#22d3ee] text-[10px] px-1.5 py-1"
                  />
                </div>
                <div className="w-16">
                  <label className="block text-[9px] uppercase text-[#22d3ee]/60">H m</label>
                  <input
                    type="number" min="0" step="0.5"
                    value={placement.mountHeight}
                    onChange={e => setPlacement(p => p && { ...p, mountHeight: e.target.value === '' ? '' : parseFloat(e.target.value) })}
                    title="Mount height (mount_height_m) — persisted on the feed config"
                    className="w-full bg-black border border-[#22d3ee]/40 text-[#22d3ee] text-[10px] px-1.5 py-1"
                  />
                </div>
              </div>
              {placement.snapDist == null && (
                <div className="border border-[#facc15]/40 bg-[#facc15]/5 p-1.5">
                  <div className="text-[9px] uppercase text-[#facc15]/80 mb-1">
                    No road within 150 m — register one through this point (500 m along facing heading):
                  </div>
                  <div className="flex items-center gap-1.5">
                    <input
                      value={newRoadName}
                      onChange={e => setNewRoadName(e.target.value)}
                      placeholder={`Road ${roads.length + 1}`}
                      className="flex-1 bg-black border border-[#22d3ee]/40 text-[#22d3ee] text-[10px] px-1.5 py-1 outline-none placeholder:text-[#22d3ee]/30"
                    />
                    <button
                      disabled={registeringRoad}
                      onClick={handleRegisterRoad}
                      className="text-[9px] py-1 px-2 border border-[#facc15]/60 text-[#facc15] uppercase font-bold hover:bg-[#facc15] hover:text-black transition-all disabled:opacity-50 whitespace-nowrap"
                      title="POST /api/v1/roads/ — polyline through the clicked point, auto-assigns this feed"
                    >
                      <MapIcon size={10} className="inline mr-1" />
                      {registeringRoad ? 'Registering…' : 'Register road'}
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

          <div className="flex items-center gap-2">
            <button
              disabled={placingSaving || !placement}
              onClick={handleSavePlacement}
              className="flex-1 text-[10px] py-1.5 border border-[#22d3ee] text-[#22d3ee] font-bold uppercase hover:bg-[#22d3ee] hover:text-black transition-all disabled:opacity-50"
            >
              {placingSaving ? 'Saving…' : 'Save placement'}
            </button>
            <button
              onClick={() => { setPlacingFeedId(''); setPlacement(null); setPlacingMsg(''); placeLayer.current.clearLayers(); }}
              className="text-[10px] py-1.5 px-2 border border-[#ff3e3e]/60 text-[#ff3e3e] uppercase font-bold hover:bg-[#ff3e3e] hover:text-black transition-all"
            >
              Cancel
            </button>
          </div>
          {placingMsg && <div className="mt-1.5 text-[9px] uppercase text-[#00ff41]">{placingMsg}</div>}
        </div>
      )}

      {/* Matrix Overlay Effect */}
      <div className="absolute inset-0 pointer-events-none border-[1px] border-[#00ff41]/10 z-20 shadow-[inset_0_0_50px_rgba(0,255,65,0.05)]" />
    </div>
  );
});

export default LeafletMap;
LeafletMap.displayName = 'LeafletMap';