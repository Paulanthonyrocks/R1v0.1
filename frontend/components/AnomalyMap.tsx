'use client';

import dynamic from 'next/dynamic';
import type { ComponentType } from 'react';
import 'leaflet/dist/leaflet.css';

interface AnomalyMapProps {
  location: [number, number];
  anomaly: {
    id: number;
    type: string;
    severity: string;
    description: string;
  };
}

// Dynamically import MapComponent with SSR disabled
const DynamicMap: ComponentType<AnomalyMapProps> = dynamic(
  () => import('./MapComponent'),
  {
    ssr: false,
    loading: () => (
      <div className="h-full w-full bg-gray-700 rounded flex items-center justify-center text-gray-400">
        Loading map...
      </div>
    ),
  }
);

const AnomalyMap = (props: AnomalyMapProps) => {
  return <DynamicMap {...props} />;
};

export default AnomalyMap;