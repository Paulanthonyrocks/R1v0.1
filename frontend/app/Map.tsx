// frontend/app/Map.tsx
'use client';
import React, { useEffect, useState, useCallback } from 'react';
import ThreeGrid from '@/components/CesiumGlobe';
import type { GlobeDataPoint } from '@/components/CesiumGlobe';

// Interface for the raw data items (can be expanded)
interface TrafficDataItem {
  latitude: number;
  longitude: number;
  vehicle_count?: number;
  predicted_congestion?: number;
  [key: string]: any; // Allow other properties from API
}

const MapPage: React.FC = () => { // Changed component name to avoid conflict if 'Map' is a type
  const [globeData, setGlobeData] = useState<GlobeDataPoint[]>([]);
  const [selectedItemDetails, setSelectedItemDetails] = useState<TrafficDataItem | null>(null);

  useEffect(() => {
    const transformAndSetData = (traffic: TrafficDataItem[], predictions: TrafficDataItem[]) => {
      const transformedTraffic: GlobeDataPoint[] = traffic.map((item, index) => ({
        id: `traffic-${item.latitude}-${item.longitude}-${index}`, // Create a unique ID
        name: `Traffic: ${item.vehicle_count !== undefined ? item.vehicle_count + ' vehicles' : 'N/A'}`,
        lat: item.latitude,
        lon: item.longitude,
        altitude: 0.2, // Slightly above surface for traffic markers
        status: (item.vehicle_count || 0) > 50 ? 'running' : 'stopped', // Example: Green if > 50 vehicles
        type: 'traffic',
        originalData: item,
      }));

      const transformedPredictions: GlobeDataPoint[] = predictions.map((item, index) => ({
        id: `pred-${item.latitude}-${item.longitude}-${index}`, // Create a unique ID
        name: `Prediction: Congestion ${item.predicted_congestion?.toFixed(2) || 'N/A'}`,
        lat: item.latitude,
        lon: item.longitude,
        altitude: 0.3, // Slightly higher for prediction markers
        status: (item.predicted_congestion || 0) > 0.7 ? 'running' : 'stopped', // Example: Green if high congestion
        type: 'prediction',
        originalData: item,
      }));
      
      setGlobeData([...transformedTraffic, ...transformedPredictions]);
    };

    let trafficDataCache: TrafficDataItem[] = [];
    let predictionDataCache: TrafficDataItem[] = [];

    const fetchInitialTraffic = async () => {
      try {
        const response = await fetch('/api/v1/traffic-data');
        if (!response.ok) throw new Error('Failed to fetch traffic data');
        trafficDataCache = await response.json();
        // Ensure cache is an array even if API returns null/undefined for empty data
        if (!Array.isArray(trafficDataCache)) trafficDataCache = [];
        transformAndSetData(trafficDataCache, predictionDataCache);
      } catch (error) {
        console.error('Error fetching initial traffic data:', error);
        trafficDataCache = []; // Ensure it's an array on error
        transformAndSetData(trafficDataCache, predictionDataCache); // Update with empty traffic data
      }
    };

    const fetchPeriodicPredictions = async () => {
      try {
        const response = await fetch('/api/v1/traffic-predictions');
        if (!response.ok) throw new Error('Failed to fetch predictions');
        predictionDataCache = await response.json();
         // Ensure cache is an array
        if (!Array.isArray(predictionDataCache)) predictionDataCache = [];
        transformAndSetData(trafficDataCache, predictionDataCache);
      } catch (error) {
        console.error('Error fetching traffic predictions:', error);
        predictionDataCache = []; // Ensure it's an array on error
        transformAndSetData(trafficDataCache, predictionDataCache); // Update with empty prediction data
      }
    };

    fetchInitialTraffic();
    fetchPeriodicPredictions(); // Initial fetch for predictions too
    const intervalId = setInterval(fetchPeriodicPredictions, 300000); // 5 minutes

    return () => clearInterval(intervalId);
  }, []); // Runs once on mount

  const handleMarkerClick = (dataPoint: GlobeDataPoint) => {
    setSelectedItemDetails(dataPoint.originalData as TrafficDataItem);
  };

  const closeDetailsModal = () => {
    setSelectedItemDetails(null);
  };

  return (
    <main className="h-screen w-screen bg-background text-primary font-matrix tracking-normal p-4 flex flex-col overflow-hidden">
      <h1 className="text-2xl font-bold mb-4 uppercase text-primary-foreground">SYSTEM STATUS GLOBE</h1>
      <div className="flex-grow relative">
        <ThreeGrid
          dataPoints={globeData}
          onMarkerClick={handleMarkerClick}
        />
      </div>

      {selectedItemDetails && (
        <div
            className="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 bg-primary p-4 border-2 border-primary-foreground pixel-drop-shadow z-20"
            style={{ minWidth: '300px', maxWidth: '90vw' }} // Added min/max width
        >
          <h3 className="text-xl font-bold mb-3 text-primary-foreground uppercase">
            {selectedItemDetails.vehicle_count !== undefined ? 'Traffic Hotspot Details' : 'Congestion Prediction Details'}
          </h3>
          <div className="text-sm text-primary-foreground bg-background p-3 border border-primary-foreground max-h-60 overflow-auto tracking-normal">
            {/* Using a more structured display instead of raw JSON */}
            <p><strong>ID:</strong> {selectedItemDetails.id || 'N/A'}</p>
            <p><strong>Name:</strong> {selectedItemDetails.name || 'N/A'}</p>
            <p><strong>Latitude:</strong> {selectedItemDetails.latitude?.toFixed(4)}</p>
            <p><strong>Longitude:</strong> {selectedItemDetails.longitude?.toFixed(4)}</p>
            {selectedItemDetails.vehicle_count !== undefined && (
              <p><strong>Vehicle Count:</strong> {selectedItemDetails.vehicle_count}</p>
            )}
            {selectedItemDetails.predicted_congestion !== undefined && (
              <p><strong>Predicted Congestion:</strong> {selectedItemDetails.predicted_congestion.toFixed(2)}</p>
            )}
            {selectedItemDetails.status && (
              <p><strong>Status:</strong> <span className={selectedItemDetails.status === 'running' ? 'text-primary-foreground font-bold' : ''}>{selectedItemDetails.status}</span></p>
            )}
             {/* Displaying other potential fields from originalData if they exist */}
            {Object.entries(selectedItemDetails.originalData || {}).map(([key, value]) => {
              if (['id', 'name', 'lat', 'lon', 'altitude', 'status', 'type', 'originalData', 'latitude', 'longitude', 'vehicle_count', 'predicted_congestion'].includes(key)) return null;
              return (
                <p key={key}><strong>{key.replace(/_/g, ' ').split(' ').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ')}:</strong> {JSON.stringify(value)}</p>
              );
            })}
          </div>
          <button
            onClick={closeDetailsModal}
            className="mt-4 px-4 py-2 bg-primary-foreground text-primary hover:bg-primary-foreground/90 font-matrix tracking-normal uppercase font-bold"
          >
            Close
          </button>
        </div>
      )}
    </main>
  );
};

export default MapPage;