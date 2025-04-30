import MatrixCard from "@/components/MatrixCard";
import MatrixButton from "@/components/MatrixButton";
import { useState, useEffect } from "react";
import dynamic from "next/dynamic";

// Dynamically import the map component to avoid SSR issues
const Map = dynamic(() => import("react-leaflet").then((mod) => mod.MapContainer), {
  ssr: false,
});
const TileLayer = dynamic(() => import("react-leaflet").then((mod) => mod.TileLayer), {
  ssr: false,
});
const Marker = dynamic(() => import("react-leaflet").then((mod) => mod.Marker), {
  ssr: false,
});
const Popup = dynamic(() => import("react-leaflet").then((mod) => mod.Popup), {
  ssr: false,
});

const Loading = () => (
  <div className="fixed inset-0 bg-matrix-bg flex items-center justify-center z-50">
    <div className="animate-pulse text-matrix text-2xl">Loading...</div>
  </div>
);

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

const initialFilter = {
  type: "all",
  severity: "all",
  location: "all",
};

const mapStyle = { height: "200px", width: "100%" };

const AnomaliesPage = () => {
  const [anomalies, setAnomalies] = useState(Anomalies);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState(initialFilter);

  useEffect(() => {
    setLoading(true);
    setTimeout(() => {
      setLoading(false);
    }, 1000);
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

const AnomaliesPage = () => {
    return (
      <div className="p-4">
        <h1 className="text-2xl font-bold mb-4 uppercase">Detected Anomalies</h1>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {anomalies.map((anomaly) => (
            <MatrixCard
              key={anomaly.id}
              header={anomaly.type}
              className={`w-full ${anomaly.resolved ? "opacity-50" : ""} border-2 border-${anomaly.severity === "high" ? "red-500" : anomaly.severity === "medium" ? "yellow-500" : "green-500"}`}
              style={{ animation: anomaly.severity === "high" ? "pulse 2s infinite" : "none" }}
            >
              <div className="flex flex-col">
                <p className="text-sm">
                  <span className="font-semibold">Description:</span> {anomaly.description}
                </p>
                <p className="mt-2 text-xs text-matrix-muted-text">
                  <span className="font-semibold">Timestamp:</span> {anomaly.timestamp}
                </p>
                <Map center={anomaly.location} zoom={13} style={mapStyle} className="mt-2 z-0">
                  <TileLayer url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" />
                  <Marker position={anomaly.location}>
                    <Popup>Anomaly {anomaly.id}</Popup>
                  </Marker>
                </Map>
                <div className="flex justify-end mt-2">
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
      </div>
    );
  };
};

export default AnomaliesPage;
