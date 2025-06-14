// src/components/MapComponent.tsx
'use client'; // Add useId import
import React, { useEffect, useRef, useId } from 'react'; // Removed useState
import L from 'leaflet';
import type { Anomaly } from '@/app/anomalies/page'; // Assuming Anomaly type is exported from page.tsx


// Import or define custom icon images
import iconRetinaUrl from 'leaflet/dist/images/marker-icon-2x.png';
import iconDefaultUrl from 'leaflet/dist/images/marker-icon.png';
import shadowUrl from 'leaflet/dist/images/marker-shadow.png';

// --- Custom Icons based on Severity ---
// The '_severity' parameter is kept for future extension where different icon images might be used.
// Prefixing with an underscore signals to the linter that it's intentionally not used in the current function body.
// eslint-disable-next-line @typescript-eslint/no-unused-vars
const createSeverityIcon = (_severity: "low" | "medium" | "high"): L.DivIcon => {
  // For now, all severities use the same generic black 1-bit marker.
  return new L.DivIcon({
    html: `<div style="background-color: black; width: 12px; height: 12px; border-radius: 3px;"></div>`,
    className: 'map-marker-1bit', // Optional: for further CSS if needed
    iconSize: [12, 12],
    iconAnchor: [6, 6], // Anchor to the center of the square
    popupAnchor: [0, -6] // Adjust popup anchor if needed
  });
};

const createActiveAnomalyIcon = (): L.DivIcon => {
  return new L.DivIcon({
    html: `<div style="background-color: black; width: 16px; height: 16px; border-radius: 4px; outline: 2px solid hsl(var(--matrix-bg));"></div>`, // Larger, with an outline using theme's green
    className: 'map-marker-1bit-active',
    iconSize: [16, 16],
    iconAnchor: [8, 8],
    popupAnchor: [0, -8]
  });
};
// --- End Custom Icons ---


interface MapComponentProps {
  anomalies: Anomaly[];
  onMarkerClick?: (anomalyId: number) => void;
  activeAnomalyId?: number | null;
}

/* const FitBoundsToAnomalies: React.FC<{ anomalies: Anomaly[] }> = React.memo(({ anomalies }) => {
  const map = useMap();
  useEffect(() => {
    if (anomalies && anomalies.length > 0) {
      const validLocations = anomalies
        .map(a => a.location)
        .filter(loc => Array.isArray(loc) && loc.length === 2 && typeof loc[0] === 'number' && typeof loc[1] === 'number') as L.LatLngExpression[];

      if (validLocations.length > 0) {
        const bounds = L.latLngBounds(validLocations);
        map.fitBounds(bounds, { padding: [50, 50], maxZoom: 16 });
      }
    }
  }, [anomalies, map]);
  return null;
}); */


const MapComponent = ({ anomalies, onMarkerClick, activeAnomalyId }: MapComponentProps) => {
  const mapRef = useRef<HTMLDivElement>(null);
  const leafletMapRef = useRef<L.Map | null>(null); // Ref to store Leaflet map instance
  const mapId = useId(); // <--- Generate a unique ID for this component instance

  // Effect to initialize and manage map lifecycle (runs when mapId or initial data changes)
  useEffect(() => {
    // L.Icon.Default.mergeOptions removed as we are using L.DivIcon exclusively for these markers.
    if (mapRef.current && !leafletMapRef.current) {
      // Initialize the map if the container exists and map is not already initialized.
      // Use a default center/zoom if no anomalies are present initially.
      leafletMapRef.current = L.map(mapRef.current, {
        center: anomalies.length > 0 && anomalies[0].location?.length === 2 ? [anomalies[0].location[0], anomalies[0].location[1]] : [0, 0],
        zoom: anomalies.length > 0 ? 13 : 2,
        scrollWheelZoom: true, // Enable mouse wheel zooming
      });

      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
      }).addTo(leafletMapRef.current);
    }

    // We need to manage markers manually now
    // Keep track of created markers to remove them later
    const markers: L.Marker[] = [];

    if (leafletMapRef.current) {
      // Clear existing markers when anomalies data changes
      leafletMapRef.current.eachLayer(layer => {
        if (layer instanceof L.Marker) {
          leafletMapRef.current?.removeLayer(layer);
        }
      });

      anomalies.forEach(anomaly => {
        if (anomaly.location && Array.isArray(anomaly.location) && anomaly.location.length === 2) {
          const marker = L.marker([anomaly.location[0], anomaly.location[1]], {
            icon: activeAnomalyId === anomaly.id
              ? createActiveAnomalyIcon()
              : createSeverityIcon(anomaly.severity),
}).addTo(leafletMapRef.current!);

          // Add a popup
          marker.bindPopup(`
            <div>
              <h3 className="text-lg font-bold">${anomaly.type}</h3>
              <p>Severity: ${anomaly.severity}</p>
              <p>${anomaly.description}</p>
              <p>${new Date(anomaly.timestamp).toLocaleString()}</p>
            </div>
          `);

          // Add click handler
          marker.on('click', () => {
            onMarkerClick?.(anomaly.id);
          });

          markers.push(marker); // Store reference
        }
      });
    }

    // Cleanup function to remove the map when the component unmounts or mapId changes
    return () => {
      if (leafletMapRef.current) {
        // Clean up markers before removing the map
        markers.forEach(marker => {
          leafletMapRef.current?.removeLayer(marker);
        });
        leafletMapRef.current.remove();
        leafletMapRef.current = null;
      }
    };
  }, [mapId, anomalies, activeAnomalyId, onMarkerClick]); // Depend on data, active anomaly, and click handler

  return (
    <div ref={mapRef} key={mapId} // Key forces div remount if mapId changes
        className="h-full w-full" // Ensure this class correctly applies height: 100%
       style={{ height: '100%', width: '100%' }} // Ensure height/width are set
    >
    </div>
  );
};

MapComponent.displayName = 'MapComponent'; // Add display name for debugging

export default MapComponent;