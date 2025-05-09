// src/components/MapComponent.tsx
'use client';

import { useEffect } from 'react';
import { MapContainer, TileLayer, Marker, Popup, useMap } from 'react-leaflet';
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
const createSeverityIcon = (_severity: "low" | "medium" | "high") => {
  // Placeholder: In a real implementation, you might choose different icon URLs based on _severity:
  // let currentIconUrl = iconDefaultUrl.src;
  // if (_severity === 'high') currentIconUrl = '/path/to/your/red-marker.png';
  // else if (_severity === 'medium') currentIconUrl = '/path/to/your/orange-marker.png';
  // etc.

  return new L.Icon({
    iconUrl: iconDefaultUrl.src, // Use currentIconUrl if implementing different images
    iconRetinaUrl: iconRetinaUrl.src,
    shadowUrl: shadowUrl.src,
    iconSize: [25, 41],
    iconAnchor: [12, 41],
    popupAnchor: [1, -34],
    shadowSize: [41, 41],
  });
};
// --- End Custom Icons ---


interface MapComponentProps {
  anomalies: Anomaly[];
  onMarkerClick?: (anomalyId: number) => void;
  activeAnomalyId?: number | null;
}

const FitBoundsToAnomalies: React.FC<{ anomalies: Anomaly[] }> = ({ anomalies }) => {
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
};


const MapComponent = ({ anomalies, onMarkerClick, activeAnomalyId }: MapComponentProps) => {
  useEffect(() => {
    L.Icon.Default.mergeOptions({
      iconRetinaUrl: iconRetinaUrl.src,
      iconUrl: iconDefaultUrl.src,
      shadowUrl: shadowUrl.src,
    });
  }, []);

  const initialCenter: [number, number] = anomalies.length > 0 && anomalies[0].location?.length === 2 ? anomalies[0].location : [0, 0];
  const initialZoom = anomalies.length > 0 ? 13 : 2;

  return (
    <div className="h-full w-full">
      <MapContainer
        center={initialCenter}
        zoom={initialZoom}
        scrollWheelZoom={true}
        className="h-full w-full"
      >
        <TileLayer
          attribution='© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        {anomalies.map((anomaly) => {
          if (!Array.isArray(anomaly.location) || anomaly.location.length !== 2 || typeof anomaly.location[0] !== 'number' || typeof anomaly.location[1] !== 'number') {
            console.warn(`Invalid location data for anomaly ID ${anomaly.id}:`, anomaly.location);
            return null;
          }
          // Pass the actual severity to the icon creation function
          const customIcon = createSeverityIcon(anomaly.severity);
          const isActive = activeAnomalyId === anomaly.id;

          return (
            <Marker
              key={anomaly.id}
              position={anomaly.location}
              icon={customIcon}
              eventHandlers={{
                click: () => {
                  onMarkerClick?.(anomaly.id);
                },
              }}
              zIndexOffset={isActive ? 1000 : 0}
            >
              <Popup>
                <b>{anomaly.type}</b> (Severity: {anomaly.severity})<br />
                {anomaly.description.substring(0, 50)}...<br />
                Lat: {anomaly.location[0].toFixed(5)}, Lon: {anomaly.location[1].toFixed(5)}
                {isActive && <><br /><b className="text-matrix-green">Selected Anomaly</b></>}
              </Popup>
            </Marker>
          );
        })}
        <FitBoundsToAnomalies anomalies={anomalies} />
      </MapContainer>
    </div>
  );
};

export default MapComponent;