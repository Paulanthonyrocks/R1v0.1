// src/components/MapComponent.tsx
'use client';
import React from 'react';

import ThreeGrid from '@/components/CesiumGlobe';
// Assuming GlobeDataPoint is exported from CesiumGlobe.tsx or a shared types file
import type { GlobeDataPoint } from '@/components/CesiumGlobe';
import type { Anomaly } from '@/app/anomalies/page'; // Keep this type

interface MapComponentProps {
  anomalies: Anomaly[];
  onMarkerClick?: (anomalyId: number) => void;
  activeAnomalyId?: number | null;
}

const MapComponent = ({ anomalies, onMarkerClick, activeAnomalyId }: MapComponentProps) => {
  // Transform Anomaly[] to GlobeDataPoint[]
  const globeDataPoints: GlobeDataPoint[] = anomalies.map(anomaly => {
    let status = 'stopped'; // Default status for ThreeGrid markers (black)
    if (anomaly.id === activeAnomalyId) {
      status = 'running'; // Use 'running' status for green color to indicate active
    }
    // Optional: map severity to status if desired for non-active anomalies
    // else if (anomaly.severity === 'high') status = 'error';
    // else if (anomaly.severity === 'medium') status = 'starting'; // Example mapping

    return {
      id: String(anomaly.id), // Convert number id to string for GlobeDataPoint compatibility
      name: anomaly.type,     // Use anomaly type as the name/label
      lat: anomaly.location[0],
      lon: anomaly.location[1],
      altitude: 0.5, // Default altitude for visibility, adjust as needed (was 1)
      status: status,
      type: 'anomaly', // Add a type for potential future differentiation
      // Pass original anomaly data if needed by onMarkerClick or for popups (if we add them to ThreeGrid)
      originalAnomaly: anomaly
    };
  });

  // Handle marker clicks from ThreeGrid
  const handleGlobeMarkerClick = (dataPoint: GlobeDataPoint) => {
    // The onMarkerClick prop expects a number (the original anomaly.id)
    if (onMarkerClick && dataPoint.originalAnomaly && typeof dataPoint.originalAnomaly.id === 'number') {
      onMarkerClick(dataPoint.originalAnomaly.id);
    } else if (onMarkerClick) {
      // Fallback if originalAnomaly.id is not available or not a number, try converting dataPoint.id
      const numericId = Number(dataPoint.id);
      if (!isNaN(numericId)) {
        onMarkerClick(numericId);
      } else {
        console.warn("MapComponent: Could not determine numeric ID for clicked marker", dataPoint);
      }
    }
  };

  return (
    <div className="h-full w-full"> {/* Ensure container has dimensions */}
      <ThreeGrid
        dataPoints={globeDataPoints}
        onMarkerClick={onMarkerClick ? handleGlobeMarkerClick : undefined}
      />
    </div>
  );
};

MapComponent.displayName = 'MapComponent';
export default MapComponent;