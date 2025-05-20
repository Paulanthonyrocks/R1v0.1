import React, { useEffect, useRef, useState, useMemo } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import 'leaflet.markercluster/dist/MarkerCluster.css';
import 'leaflet.markercluster/dist/MarkerCluster.Default.css';
import 'leaflet.markercluster';
import 'leaflet.heat';
import { cn } from '@/lib/utils'; // Adjust the import based on your project structure
import { usePathname } from 'next/navigation';
import io from 'socket.io-client';

// It's good practice to define icons if you're using markers,
// though circles don't strictly need this, it avoids some common Leaflet issues.
// If you were using L.marker, you'd need this:
// import iconRetinaUrl from 'leaflet/dist/images/marker-icon-2x.png';
// import iconUrl from 'leaflet/dist/images/marker-icon.png';
// import shadowUrl from 'leaflet/dist/images/marker-shadow.png';

// L.Icon.Default.mergeOptions({
//   iconRetinaUrl,
//   iconUrl,
//   shadowUrl,
// });


interface TrafficDataItem {
  latitude: number;
  longitude: number;
  vehicle_count: number;
  // You might also want sensor_id, timestamp, congestion_score etc.
  // depending on what your API returns and what you want to display.
  // sensor_id?: string;
  // timestamp?: number;
  // congestion_score?: number;
}

// Mock incident data (replace with real API/WebSocket later)
interface IncidentDataItem {
  id: string;
  latitude: number;
  longitude: number;
  type: string;
  description: string;
  severity: 'low' | 'medium' | 'high';
  timestamp: string;
}

const incidentIcon = new L.Icon({
  iconUrl: 'https://cdn-icons-png.flaticon.com/512/565/565547.png', // Example warning icon
  iconSize: [32, 32],
  iconAnchor: [16, 32],
  popupAnchor: [0, -32],
});

// Camera marker data (can be extended with unique feeds later)
const CAMERA_MARKERS = [
  {
    id: 'cam-1',
    name: 'Main Street Camera',
    latitude: 51.509,
    longitude: -0.095,
    videoUrl: '/api/v1/sample-video',
    description: 'Intersection at Main St. & 1st Ave.',
    area: 'Downtown',
    status: 'Active',
  },
  {
    id: 'cam-2',
    name: 'Downtown Camera',
    latitude: 51.507,
    longitude: -0.087,
    videoUrl: '/api/v1/sample-video',
    description: 'Downtown roundabout.',
    area: 'Downtown',
    status: 'Active',
  },
  {
    id: 'cam-3',
    name: 'Riverside Camera',
    latitude: 51.511,
    longitude: -0.1,
    videoUrl: '/api/v1/sample-video',
    description: 'Riverside bridge.',
    area: 'Riverside',
    status: 'Offline',
  },
  {
    id: 'cam-4',
    name: 'North Gate Camera',
    latitude: 51.513,
    longitude: -0.12,
    videoUrl: '/api/v1/sample-video',
    description: 'North gate entrance.',
    area: 'North',
    status: 'Active',
  },
  {
    id: 'cam-5',
    name: 'Parkside Camera',
    latitude: 51.508,
    longitude: -0.11,
    videoUrl: '/api/v1/sample-video',
    description: 'Parkside avenue.',
    area: 'Parkside',
    status: 'Active',
  }
];

const cameraIcon = new L.Icon({
  iconUrl: 'https://cdn-icons-png.flaticon.com/512/149/149995.png', // Camera icon
  iconSize: [32, 32],
  iconAnchor: [16, 32],
  popupAnchor: [0, -32],
});

const AREAS = Array.from(new Set(CAMERA_MARKERS.map(c => c.area)));
const STATUSES = Array.from(new Set(CAMERA_MARKERS.map(c => c.status)));

const MapComponent = () => { // Renamed to avoid conflict with built-in Map type
  const mapContainerRef = useRef<HTMLDivElement | null>(null); // Ref for the map container div
  const mapInstanceRef = useRef<L.Map | null>(null); // Ref to store the map instance
  const markersRef = useRef<L.Circle[]>([]); // Store references to current markers
  const incidentMarkersRef = useRef<L.Marker[]>([]); // Store references to incident markers
  const pathname = usePathname();
  const [trafficData, setTrafficData] = useState<TrafficDataItem[]>([]);
  const [showCameraModal, setShowCameraModal] = useState(false);
  const [activeCamera, setActiveCamera] = useState<typeof CAMERA_MARKERS[0] | null>(null);
  const [videoLoading, setVideoLoading] = useState(false);
  const [videoError, setVideoError] = useState<string | null>(null);
  const [fullscreen, setFullscreen] = useState(false);
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [filter, setFilter] = useState('');
  const [areaFilter, setAreaFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const modalRef = useRef<HTMLDivElement | null>(null);
  const clusterGroupRef = useRef<L.LayerGroup | null>(null);
  const [incidents, setIncidents] = useState<IncidentDataItem[]>([]);
  const [showIncidents, setShowIncidents] = useState(true);
  const [showCameras, setShowCameras] = useState(true);
  const [showAnalytics, setShowAnalytics] = useState(true);
  const [heatmapLayer, setHeatmapLayer] = useState<L.Layer | null>(null);
  const [kpiData, setKpiData] = useState<Record<string, any>[]>([]);
  const [selectedCameraId, setSelectedCameraId] = useState<string | null>(null);

  const filteredCameras = useMemo(() => (
    showCameras ? CAMERA_MARKERS.filter(cam =>
      cam.name.toLowerCase().includes(filter.toLowerCase()) &&
      (areaFilter ? cam.area === areaFilter : true) &&
      (statusFilter ? cam.status === statusFilter : true)
    ) : []
  ), [showCameras, filter, areaFilter, statusFilter]);

  // Initialize map and set up WebSocket connection
  useEffect(() => {
    let socket: ReturnType<typeof io> | null = null;
    if (mapContainerRef.current && !mapInstanceRef.current) {
      // Initialize the map
      const map = L.map(mapContainerRef.current).setView([51.505, -0.09], 13);
      mapInstanceRef.current = map;

      // Add OpenStreetMap tiles
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
      }).addTo(map);

      // Fetch and display initial traffic data
      const fetchTrafficData = async () => {
        if (!mapInstanceRef.current) return;
        try {
          const response = await fetch('/api/v1/traffic-data');
          if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
          }
          const data: TrafficDataItem[] = await response.json();
          setTrafficData(data); // Set initial data
        } catch (error) {
          console.error('Error fetching or processing traffic data:', error);
        }
      };
      fetchTrafficData();

      // Set up WebSocket for real-time updates
      socket = io('/api/traffic/live');
      socket.on('connect', () => {
        console.log('WebSocket connected!');
      });
      socket.on('trafficUpdate', (data: TrafficDataItem[]) => {
        setTrafficData(data); // Update state with new data
      });
    }
    // Cleanup function
    return () => {
      if (mapInstanceRef.current) {
        mapInstanceRef.current.remove();
        mapInstanceRef.current = null;
      }
      if (socket) {
        socket.disconnect();
      }
    };
  }, []);

  // Fetch incidents from backend
  useEffect(() => {
    const fetchIncidents = async () => {
      try {
        const response = await fetch('/api/v1/incidents');
        if (!response.ok) throw new Error('Failed to fetch incidents');
        const data = await response.json();
        setIncidents(data);
      } catch (err: unknown) {
        if (err instanceof Error) {
          console.error('Error fetching incidents:', err.message);
        } else {
          console.error('Unknown error fetching incidents:', err);
        }
      }
    };
    fetchIncidents();
    const interval = setInterval(fetchIncidents, 10000);
    return () => clearInterval(interval);
  }, []);

  // Fetch sample feed ID and KPIs
  useEffect(() => {
    const fetchKpis = async () => {
      try {
        // Get feeds
        const feedsRes = await fetch('/api/v1/feeds');
        if (!feedsRes.ok) throw new Error('Failed to fetch feeds');
        const feeds = await feedsRes.json();
        // Find sample feed (fallback to first running feed)
        const sampleFeed = feeds.find((f: Record<string, unknown>) => f.status === 'running') || feeds[0];
        if (!sampleFeed) return;
        // Get KPIs
        const kpiRes = await fetch(`/api/v1/feeds/${sampleFeed.feed_id}/kpis`);
        if (!kpiRes.ok) return;
        const kpis = await kpiRes.json();
        setKpiData(Array.isArray(kpis) ? kpis : [kpis]);
      } catch (error: unknown) {
        setKpiData([]);
      }
    };
    if (showAnalytics) {
      fetchKpis();
      const interval = setInterval(fetchKpis, 10000);
      return () => clearInterval(interval);
    }
  }, [showAnalytics]);

  // Effect to update map markers and overlays
  useEffect(() => {
    if (!mapInstanceRef.current) return;
    // Remove old traffic markers
    markersRef.current.forEach(marker => marker.remove());
    markersRef.current = [];
    // Add new traffic markers
    trafficData.forEach((item) => {
      const { latitude, longitude, vehicle_count } = item;
      const marker = L.circle([latitude, longitude], {
        color: vehicle_count > 50 ? 'orange' : (vehicle_count > 20 ? 'yellow' : 'green'),
        fillColor: vehicle_count > 50 ? '#f03' : (vehicle_count > 20 ? '#f90' : '#0f0'),
        fillOpacity: 0.5,
        radius: vehicle_count * 10 + 50
      }).addTo(mapInstanceRef.current!).bindPopup(`Vehicles: ${vehicle_count}`);
      markersRef.current.push(marker);
    });
    // Remove old heatmap
    if (heatmapLayer && mapInstanceRef.current.hasLayer(heatmapLayer)) {
      mapInstanceRef.current.removeLayer(heatmapLayer);
      setHeatmapLayer(null);
    }
    // Add heatmap overlay if enabled
    if (showAnalytics && kpiData.length > 0 && (window as unknown as { L: typeof L }).L && (L as unknown as { heatLayer: any }).heatLayer) {
      const heatPoints = kpiData
        .filter((k) => k.latitude && k.longitude && k.congestion_index !== undefined)
        .map((k) => [k.latitude, k.longitude, Math.max(0.1, k.congestion_index)]);
      if (heatPoints.length > 0) {
        const heat = (L as unknown as { heatLayer: any }).heatLayer(heatPoints, { radius: 40, blur: 25, maxZoom: 17, minOpacity: 0.3 }).addTo(mapInstanceRef.current!);
        setHeatmapLayer(heat);
      }
    }
    // Remove old incident markers
    incidentMarkersRef.current.forEach(marker => marker.remove());
    incidentMarkersRef.current = [];
    // Add new incident markers if enabled
    if (showIncidents) {
      incidents.forEach((incident) => {
        const marker = L.marker([incident.latitude, incident.longitude], { icon: incidentIcon })
          .addTo(mapInstanceRef.current!)
          .bindPopup(
            `<div style='min-width:180px;'>
              <strong>${incident.type}</strong><br/>
              <span>${incident.description}</span><br/>
              <span style='font-size:0.8em;color:#888;'>${new Date(incident.timestamp).toLocaleString()}</span>
            </div>`
          );
        incidentMarkersRef.current.push(marker);
      });
    }
    // Camera markers logic
    if (!mapInstanceRef.current) return;
    if (clusterGroupRef.current && mapInstanceRef.current.hasLayer(clusterGroupRef.current)) {
      mapInstanceRef.current.removeLayer(clusterGroupRef.current);
      clusterGroupRef.current = null;
    }
    const clusterGroup = (L as any).markerClusterGroup();
    clusterGroupRef.current = clusterGroup;
    // Filter cameras
    filteredCameras.forEach((cam) => {
      const marker = L.marker([cam.latitude, cam.longitude], { icon: cameraIcon })
        .bindPopup(`<b>${cam.name}</b><br>Click for live feed.`);
      marker.on('click', () => {
        setActiveCamera(cam);
        setShowCameraModal(true);
        setVideoLoading(true);
        setVideoError(null);
        setFullscreen(false);
        setSelectedCameraId(cam.id);
      });
      clusterGroup.addLayer(marker);
    });
    clusterGroup.addTo(mapInstanceRef.current!);
    // Zoom to marker if selected
    if (selectedCameraId) {
      const cam = CAMERA_MARKERS.find(c => c.id === selectedCameraId);
      if (cam) {
        mapInstanceRef.current.setView([cam.latitude, cam.longitude], 16, { animate: true });
      }
    }
    // Cleanup cluster group on rerender
    return () => {
      clusterGroup.clearLayers();
      if (mapInstanceRef.current && mapInstanceRef.current.hasLayer(clusterGroup)) {
        mapInstanceRef.current.removeLayer(clusterGroup);
      }
      clusterGroupRef.current = null;
    };
  }, [trafficData, filter, areaFilter, statusFilter, incidents, showIncidents, showCameras, showAnalytics, kpiData, selectedCameraId, filteredCameras]);

  // Fullscreen handler
  const handleFullscreen = () => {
    if (videoRef.current) {
      if (!fullscreen) {
        if (videoRef.current.requestFullscreen) {
          videoRef.current.requestFullscreen();
        }
        setFullscreen(true);
      } else {
        if (document.exitFullscreen) {
          document.exitFullscreen();
        }
        setFullscreen(false);
      }
    }
  };

  // Listen for fullscreen change to sync state
  useEffect(() => {
    const onFsChange = () => {
      const isFs = !!document.fullscreenElement;
      setFullscreen(isFs);
    };
    document.addEventListener('fullscreenchange', onFsChange);
    return () => document.removeEventListener('fullscreenchange', onFsChange);
  }, []);

  // Modal accessibility: focus trap and esc-to-close
  useEffect(() => {
    if (!showCameraModal) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setShowCameraModal(false);
        setActiveCamera(null);
      }
      // Focus trap
      if (e.key === 'Tab' && modalRef.current) {
        const focusableEls = modalRef.current.querySelectorAll<HTMLElement>(
          'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
        );
        const firstEl = focusableEls[0];
        const lastEl = focusableEls[focusableEls.length - 1];
        if (!e.shiftKey && document.activeElement === lastEl) {
          e.preventDefault();
          firstEl.focus();
        } else if (e.shiftKey && document.activeElement === firstEl) {
          e.preventDefault();
          lastEl.focus();
        }
      }
    };
    document.addEventListener('keydown', handleKeyDown);
    // Focus modal on open
    setTimeout(() => { modalRef.current?.focus(); }, 0);
    return () => document.removeEventListener('keydown', handleKeyDown);
  }, [showCameraModal]);

  // UI controls
  const handleResetFilters = () => {
    setFilter('');
    setAreaFilter('');
    setStatusFilter('');
    setSelectedCameraId(null);
  };

  // Note: The div now uses mapContainerRef instead of a direct id="map"
  // This is generally a more React-idiomatic way to handle DOM elements.
  return (
    <main
      className={cn(
        // ...other classes...
        pathname !== '/' && 'pt-16'
      )}
    >
      <div style={{ position: 'absolute', top: 16, left: 16, zIndex: 1100, background: '#fff', borderRadius: 8, boxShadow: '0 2px 8px #0002', padding: 8, display: 'flex', flexWrap: 'wrap', gap: 8 }}>
        <input
          type="text"
          placeholder="Filter cameras..."
          value={filter}
          onChange={e => setFilter(e.target.value)}
          style={{ padding: 6, borderRadius: 4, border: '1px solid #ccc', minWidth: 140, marginRight: 8 }}
        />
        <select value={areaFilter} onChange={e => setAreaFilter(e.target.value)} style={{ padding: 6, borderRadius: 4, border: '1px solid #ccc', minWidth: 100 }} aria-label="Filter by area">
          <option value="">All Areas</option>
          {AREAS.map(area => <option key={area} value={area}>{area}</option>)}
        </select>
        <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)} style={{ padding: 6, borderRadius: 4, border: '1px solid #ccc', minWidth: 100 }} aria-label="Filter by status">
          <option value="">All Statuses</option>
          {STATUSES.map(status => <option key={status} value={status}>{status}</option>)}
        </select>
        <button onClick={handleResetFilters} style={{ padding: 6, borderRadius: 4, border: '1px solid #ccc', background: '#eee', minWidth: 100 }}>Reset Filters</button>
        <button onClick={() => { if (filteredCameras.length > 0) setSelectedCameraId(filteredCameras[0].id); }} style={{ padding: 6, borderRadius: 4, border: '1px solid #ccc', background: '#eee', minWidth: 100 }}>Zoom to Marker</button>
        <label style={{ display: 'flex', alignItems: 'center', gap: 4 }}><input type="checkbox" checked={showIncidents} onChange={e => setShowIncidents(e.target.checked)} /> Incidents</label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 4 }}><input type="checkbox" checked={showCameras} onChange={e => setShowCameras(e.target.checked)} /> Cameras</label>
        <label style={{ display: 'flex', alignItems: 'center', gap: 4 }}><input type="checkbox" checked={showAnalytics} onChange={e => setShowAnalytics(e.target.checked)} /> Analytics</label>
      </div>
      {/* Analytics summary widget */}
      {showAnalytics && kpiData.length > 0 && (
        <div style={{ position: 'absolute', top: 80, left: 16, zIndex: 1100, background: '#fff', borderRadius: 8, boxShadow: '0 2px 8px #0002', padding: 12, minWidth: 180 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Analytics Summary</div>
          {kpiData.map((k, i) => (
            <div key={i} style={{ fontSize: 14 }}>
              Congestion Index: <b>{k.congestion_index !== undefined ? k.congestion_index.toFixed(2) : 'N/A'}</b><br />
              Vehicles: <b>{k.vehicle_count !== undefined ? k.vehicle_count : 'N/A'}</b><br />
              {k.speed !== undefined && <>Speed: <b>{k.speed.toFixed(1)} km/h</b><br /></>}
            </div>
          ))}
        </div>
      )}
      <div ref={mapContainerRef} style={{ height: '100vh', width: '100%' }} />
      {showCameraModal && activeCamera && (
        <div
          ref={modalRef}
          tabIndex={-1}
          aria-modal="true"
          role="dialog"
          aria-label={activeCamera.name + ' video modal'}
          style={{
            position: 'fixed', top: 0, left: 0, width: '100vw', height: '100vh',
            background: 'rgba(0,0,0,0.7)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
            transition: 'background 0.3s',
          }}
          onClick={() => { setShowCameraModal(false); setActiveCamera(null); }}
        >
          <div
            style={{
              background: '#222', padding: 16, borderRadius: 8, minWidth: 280, minHeight: 180, position: 'relative', maxWidth: '96vw', maxHeight: '96vh', boxShadow: '0 4px 32px #0008',
              width: '100%',
              display: 'flex', flexDirection: 'column', alignItems: 'center',
              transition: 'transform 0.25s cubic-bezier(.4,2,.6,1)',
            }}
            onClick={e => e.stopPropagation()}
          >
            <button aria-label="Close" style={{ position: 'absolute', top: 8, right: 8, background: 'transparent', color: '#fff', border: 'none', fontSize: 28, cursor: 'pointer', lineHeight: 1 }} onClick={() => { setShowCameraModal(false); setActiveCamera(null); }}>&times;</button>
            <div style={{ color: '#fff', fontWeight: 600, fontSize: 18, marginBottom: 8, textAlign: 'center', wordBreak: 'break-word' }}>{activeCamera.name}</div>
            <div style={{ position: 'relative', width: '100%', display: 'flex', justifyContent: 'center' }}>
              <video
                ref={videoRef}
                src={activeCamera.videoUrl}
                controls
                autoPlay
                style={{ width: '100%', maxWidth: 480, maxHeight: '60vh', background: '#000', borderRadius: 4, display: 'block', margin: '0 auto' }}
                onLoadedData={() => setVideoLoading(false)}
                onError={() => { setVideoLoading(false); setVideoError('Failed to load video.'); }}
              />
              {/* Fullscreen button */}
              <button
                aria-label={fullscreen ? 'Exit Fullscreen' : 'Fullscreen'}
                onClick={handleFullscreen}
                style={{ position: 'absolute', bottom: 12, right: 12, background: 'rgba(0,0,0,0.6)', color: '#fff', border: 'none', borderRadius: 4, padding: '4px 10px', fontSize: 16, cursor: 'pointer', zIndex: 2 }}
              >
                {fullscreen ? 'Exit Fullscreen' : 'Fullscreen'}
              </button>
              {/* Loading spinner */}
              {videoLoading && !videoError && (
                <div style={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%,-50%)', zIndex: 2 }}>
                  <div className="animate-spin rounded-full h-12 w-12 border-t-2 border-b-2 border-white" style={{ borderTopColor: '#fff', borderBottomColor: '#fff', borderLeftColor: 'transparent', borderRightColor: 'transparent' }} />
                </div>
              )}
              {/* Error message */}
              {videoError && (
                <div style={{ position: 'absolute', top: '50%', left: '50%', transform: 'translate(-50%,-50%)', color: 'red', background: '#222', padding: 12, borderRadius: 8, zIndex: 2 }}>
                  {videoError}
                </div>
              )}
            </div>
            <div style={{ color: '#bbb', marginTop: 8, textAlign: 'center', fontSize: 14, wordBreak: 'break-word' }}>{activeCamera.description}</div>
          </div>
        </div>
      )}
    </main>
  );
};

export default MapComponent;