"use client";
import React from 'react';
import dynamic from 'next/dynamic';
import type { Anomaly } from './anomalies/page';
import 'leaflet/dist/leaflet.css';

const MapContainer = dynamic(
  () => import('react-leaflet').then((mod) => mod.MapContainer),
  { ssr: false }
) as unknown as typeof import('react-leaflet').MapContainer;

const TileLayer = dynamic(
  () => import('react-leaflet').then((mod) => mod.TileLayer),
  { ssr: false }
) as unknown as typeof import('react-leaflet').TileLayer;

const Marker = dynamic(
  () => import('react-leaflet').then((mod) => mod.Marker),
  { ssr: false }
) as unknown as typeof import('react-leaflet').Marker;

const Popup = dynamic(
  () => import('react-leaflet').then((mod) => mod.Popup),
  { ssr: false }
) as unknown as typeof import('react-leaflet').Popup;

interface AnomalyMapProps {
  anomaly: Anomaly;
}

const AnomalyMap: React.FC<AnomalyMapProps> = ({ anomaly }) => {
  return (
    <div className="h-[250px] w-full mt-3 mb-2 bg-gray-700 rounded overflow-hidden">
      <MapContainer
        center={[51.505, -0.09]}
        zoom={13}
        className="map-container"
        scrollWheelZoom={false}
      >
        <TileLayer
          attribution='© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        />
        <Marker position={anomaly.location}>
          <Popup>
            <b>{anomaly.type}</b><br />ID: {anomaly.id}<br />Severity: {anomaly.severity}
          </Popup>
        </Marker>
      </MapContainer>
    </div>
  );
};

export default AnomalyMap;