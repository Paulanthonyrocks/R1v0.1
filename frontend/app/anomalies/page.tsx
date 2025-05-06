"use client";
import React, { useState, useEffect } from 'react';
import 'leaflet/dist/leaflet.css';
import MatrixCard from "@/components/MatrixCard";
import dynamic from 'next/dynamic';
import MatrixButton from '@/components/MatrixButton';

const anomalySeverities = ["low", "medium", "high"];

const anomalyTypes = [
    "Traffic Congestion",
    "Signal Malfunction",
    "Road Closure",
    "Accident",
    "Other",
];

// Create dynamic Map component (assumed to be AnomalyMap)
// Renamed to AnomalyMapDynamic to avoid potential naming conflicts and clarify it's a dynamic import.
// The path '../../components/AnomalyMap' assumes this file is in 'app/anomalies/page.tsx'
// and AnomalyMap component is in 'components/AnomalyMap.tsx' relative to the 'app' or 'src' directory root.
// Adjust the path if your project structure is different.
const AnomalyMapDynamic = dynamic(() => import('../../components/AnomalyMap'), {
  ssr: false,
  loading: () => <div className="h-[250px] w-full bg-gray-700 rounded overflow-hidden flex items-center justify-center text-gray-400">Loading map...</div>
});

// Fix Leaflet's default icon path issue with webpack/Next.js.
// This needs to run only on the client side.
if (typeof window !== 'undefined') {
  import('leaflet').then(LModule => {
    // It's good practice to check if the imported module and its properties exist
    if (LModule && LModule.Icon && LModule.Icon.Default) {
      LModule.Icon.Default.mergeOptions({
        iconRetinaUrl: '/leaflet/dist/images/marker-icon-2x.png', // These paths must exist in the /public directory
        iconUrl: '/leaflet/dist/images/marker-icon.png',          // e.g., public/leaflet/dist/images/marker-icon.png
        shadowUrl: '/leaflet/dist/images/marker-shadow.png',
      });
    } else {
      console.warn("Leaflet L.Icon.Default not found after dynamic import. Map icons might be misconfigured.");
    }
  }).catch(error => {
    console.error("Error dynamically importing Leaflet for icon setup:", error);
  });
}

// Define Location type for clarity
type LocationTuple = [number, number];

const locations: LocationTuple[] = [
  [34.0522, -118.2437],
  [37.7749, -122.4194],
  [40.7128, -74.0060],
  [41.8781, -87.6298],
  [29.7604, -95.3698],
];

// Define Anomaly type for clarity, and export it
interface Anomaly {
  id: number;
  type: string;
  severity: "low" | "medium" | "high";
  description: string;
  timestamp: string;
  location: LocationTuple;
  resolved: boolean;
}
export type { Anomaly };

const generateAnomaly = (index: number): Anomaly => ({
  id: index,
  type: anomalyTypes[index % anomalyTypes.length],
  severity: anomalySeverities[index % anomalySeverities.length] as "low" | "medium" | "high",
  description: `Placeholder description for anomaly ${index + 1}. This is a sample description of the detected anomaly.`,
  timestamp: new Date(Date.now() - index * 1000 * 60 * 60).toLocaleString(),
  location: locations[index % locations.length],
  resolved: false,
});

const InitialAnomalies: Anomaly[] = [...Array(5)].map((_, index) => generateAnomaly(index));


const AnomaliesPage = () => {
  const [anomalies, setAnomalies] = useState<Anomaly[]>(InitialAnomalies);
  const [loading, setLoading] = useState(true);

  // The Leaflet icon fix useEffect that was present in one of the merge branches has been removed,
  // as this is now handled globally and client-side at the module level.

  useEffect(() => {
    // Simulate loading delay
    const timer = setTimeout(() => {
      setLoading(false);
    }, 500); 

    return () => clearTimeout(timer); // Cleanup timer on unmount
  }, []);

  if (loading) {
    return <Loading />;
  }

  const handleResolve = (anomalyId: number) => {
    setAnomalies((prevAnomalies) =>
      prevAnomalies.map((anomaly) =>
        anomaly.id === anomalyId ? { ...anomaly, resolved: true } : anomaly
      )
    );
  };

  const handleDismiss = (anomalyId: number) => {
    setAnomalies((prevAnomalies) => prevAnomalies.filter((anomaly) => anomaly.id !== anomalyId));
  };

  return (
    <div className="p-4">
      <h1 className="text-2xl font-bold mb-4 uppercase">Detected Anomalies</h1>
      {anomalies.length === 0 ? (
         <p className="text-matrix-muted-text">No anomalies to display.</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {anomalies.map((anomaly) => (
            <MatrixCard
              key={anomaly.id}
              title={anomaly.type}
              colorOverride={anomaly.resolved ? "hsl(0, 0%, 50%)" : anomaly.severity === "high"
                ? "hsl(0, 100%, 50%)"
                : anomaly.severity === "medium"
                  ? "hsl(39, 100%, 50%)"
                  : "hsl(120, 100%, 35%)"
              }
            >
              <div className="flex flex-col">
                <p className="text-sm mb-1">
                  <span className="font-semibold">Severity:</span> <span className={`capitalize ${
                     anomaly.severity === "high" ? "text-red-500" :
                     anomaly.severity === "medium" ? "text-yellow-500" :
                     "text-green-500"
                  }`}>{anomaly.severity}</span>
                  {anomaly.resolved && <span className="ml-2 text-gray-500">(Resolved)</span>}
                </p>
                <p className="text-sm">
                  <span className="font-semibold">Description:</span> {anomaly.description}
                </p>
                <p className="mt-2 text-xs text-matrix-muted-text">
                  <span className="font-semibold">Timestamp:</span> {anomaly.timestamp}
                </p>
                {/* Map rendering section - merged from HEAD and ceca87d... */}
                <div className="h-[250px] w-full mt-3 mb-2 bg-gray-700 rounded overflow-hidden">
                  {/* Pass both location and anomaly to AnomalyMapDynamic as AnomalyMap expects 'location' */}
                  <AnomalyMapDynamic location={anomaly.location} anomaly={anomaly} />
                </div>
                <div className="flex justify-end mt-2 space-x-2"> {/* Added space-x-2 for button spacing */}
                  {!anomaly.resolved && (
                   <MatrixButton onClick={() => handleResolve(anomaly.id)} color="green">
                        Resolve
                    </MatrixButton>
                  )}
                  <MatrixButton onClick={() => handleDismiss(anomaly.id)} color="red">
                    Dismiss
                  </MatrixButton>
                </div>
              </div>
            </MatrixCard>
          ))}
        </div>
      )}
    </div>
  );
};

const Loading = () => (
  <div className="fixed inset-0 bg-matrix-bg flex items-center justify-center z-50">
    <div className="animate-pulse text-matrix text-2xl">Loading...</div>
  </div>
);


export default AnomaliesPage;