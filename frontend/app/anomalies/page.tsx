"use client";
import "leaflet/dist/leaflet.css";
import MatrixCard from "@/components/MatrixCard";
import MatrixButton from "@/components/MatrixButton";
import { useState, useEffect } from "react";
import dynamic from "next/dynamic";





const Loading = () => (
  <div className="fixed inset-0 bg-matrix-bg flex items-center justify-center z-50">
    <div className="animate-pulse text-matrix text-2xl">Loading...</div>
  </div>
);

const MapContainer = dynamic(() => import('react-leaflet').then(mod => mod.MapContainer), { loading: () => <Loading />, ssr: false })
const TileLayer = dynamic(() => import('react-leaflet').then(mod => mod.TileLayer), { loading: () => <Loading />, ssr: false })
const Marker = dynamic(() => import('react-leaflet').then(mod => mod.Marker), { loading: () => <Loading />, ssr: false })
const Popup = dynamic(() => import('react-leaflet').then(mod => mod.Popup), { loading: () => <Loading />, ssr: false })
const useMap = dynamic(() => import('react-leaflet').then(mod => mod.useMap), { loading: () => <Loading />, ssr: false })

const anomalySeverities = ["low", "medium", "high"];

const anomalyTypes = [
  "Traffic Congestion",
  "Signal Malfunction",
  "Road Closure",
  "Accident",
  "Other",
];

const locations = [
  [34.0522, -118.2437],
  [37.7749, -122.4194],
  [40.7128, -74.0060],
  [41.8781, -87.6298],
  [29.7604, -95.3698],
];

const generateAnomaly = (index: number) => ({
  id: index,
  type: anomalyTypes[index % anomalyTypes.length],
  severity: anomalySeverities[index % anomalySeverities.length],
  description: `Placeholder description for anomaly ${index + 1}. This is a sample description of the detected anomaly.`,
  timestamp: new Date(Date.now() - index * 1000 * 60 * 60).toLocaleString(),
  location: locations[index % locations.length],
  resolved: false,
});

const Anomalies = [...Array(5)].map((_, index) => generateAnomaly(index));




const AnomaliesPage = () => {
  const [anomalies, setAnomalies] = useState(Anomalies);
  const [loading, setLoading] = useState(true);


  useEffect(() => {
    setLoading(true);
    setTimeout(() => {
      setLoading(false);
    }, 1000);
  }, []);

  function MapWrapper({ center, zoom, children }: { center: number[]; zoom: number, children: React.ReactNode }) {
    const map = useMap();

      map.setView(center, zoom);
    return (<>{children}</>);
    return (
      <></>
    );
  }
  
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
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {anomalies.map((anomaly) => (
          <MatrixCard
            key={anomaly.id}
            title={
              anomaly.type
            }
            colorOverride={anomaly.resolved ? "hsl(0, 0%, 50%)" : anomaly.severity === "high"
              ? "hsl(0, 100%, 50%)"
              : anomaly.severity === "medium"
                ? "hsl(60, 100%, 50%)"
                : "hsl(120, 100%, 50%)"
            }
          >
            <div className="flex flex-col ">

              <p className="text-sm">
                <span className="font-semibold">Description:</span> {anomaly.description}
              </p>
              <p className="mt-2 text-xs text-matrix-muted-text">
                <span className="font-semibold">Timestamp:</span> {anomaly.timestamp}
              </p>
              <div className="h-[300px] w-full">
                <MapContainer
                  style={{ height: "300px", width: "100%" }}
                  className="map-container"
                  >
                  <MapWrapper center={anomaly.location} zoom={13} >
                      <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
                      <Marker position={anomaly.location}><Popup>Anomaly {anomaly.id}</Popup></Marker>
                  </Marker>                  
                </MapContainer>
              </div>
              <div className="flex justify-end mt-2">

                {!anomaly.resolved && (
                  <MatrixButton onClick={() => handleResolve(anomaly.id)} color="green">
                    Resolve
                  </MatrixButton>
                )}
                <MatrixButton onClick={() => handleDismiss(anomaly.id)} color="red">Dismiss</MatrixButton>
              </div>
            </div>
          </MatrixCard>
        ))}
      </div>
    </div>
  );
};

export default AnomaliesPage;
