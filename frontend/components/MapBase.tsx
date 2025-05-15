// src/components/MapBase.tsx
import React, { ReactNode } from 'react';
import { MapContainer, TileLayer } from 'react-leaflet';
import 'leaflet/dist/leaflet.css';

interface MapBaseProps {
  children: ReactNode;
  initialCenter?: [number, number];
  initialZoom?: number;
}

const MapBase: React.FC<MapBaseProps> = ({
  children,
  initialCenter = [0, 0], // Default center
  initialZoom = 2, // Default zoom
}) => {
  return (
    <MapContainer
      center={initialCenter}
      zoom={initialZoom}
      scrollWheelZoom={true}
      className="h-full w-full" // Ensure map container has size
    >
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      {children}
    </MapContainer>
  );
};

export default MapBase;