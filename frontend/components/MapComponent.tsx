'use client';

import { useEffect } from 'react';
import { MapContainer, TileLayer, Marker, Popup } from 'react-leaflet';
import L from 'leaflet';

interface MapComponentProps {
  location: [number, number];
  anomaly: {
    id: number;
    type: string;
    severity: string;
    description: string;
  };
}

// Added proper typing for the icon prototype
interface IconDefault extends L.Icon.Default {
  _getIconUrl?: string;
}

const MapComponent = ({ location, anomaly }: MapComponentProps) => {
  useEffect(() => {
    // Initialize leaflet icons with proper typing
    delete (L.Icon.Default.prototype as IconDefault)._getIconUrl;
    L.Icon.Default.mergeOptions({
      iconRetinaUrl: '/leaflet/dist/images/marker-icon-2x.png',
      iconUrl: '/leaflet/dist/images/marker-icon.png',
      shadowUrl: '/leaflet/dist/images/marker-shadow.png',
    });
  }, []);

  return (
    <div className="h-full w-full">
      <MapContainer
        key={`map-${anomaly.id}-${location.join(',')}`}
        center={location}
        zoom={13}
        scrollWheelZoom={false}
        className="h-full w-full"
      >
        <TileLayer
          attribution='© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <Marker position={location}>
          <Popup>
            <b>{anomaly.type}</b><br/>
            Severity: {anomaly.severity}<br/>
            {anomaly.description}
          </Popup>
        </Marker>
      </MapContainer>
    </div>
  );
};

export default MapComponent;