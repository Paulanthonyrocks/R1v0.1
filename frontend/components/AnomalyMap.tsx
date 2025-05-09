// src/components/AnomalyMap.tsx
'use client';

import dynamic from 'next/dynamic';
import type { ComponentType } from 'react';
import 'leaflet/dist/leaflet.css';
import type { Anomaly } from '@/app/anomalies/page'; // Assuming Anomaly type is exported from page.tsx

// Updated props to accept full anomaly objects and interaction handlers
interface AnomalyMapProps {
  anomalies: Anomaly[]; // Changed from locations to full Anomaly objects
  onMarkerClick?: (anomalyId: number) => void;
  activeAnomalyId?: number | null; // To potentially highlight a marker
}

const DynamicMap: ComponentType<AnomalyMapProps> = dynamic(
  () => import('@/components/MapComponent').then(mod => mod.default ),
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