// frontend/components/anomalies/AnomalyDetailModal.tsx
import React from 'react';
import MatrixButton from "@/components/MatrixButton"; // Assuming this path is correct

// Define Anomaly type here or import from a central types file
// For this task, let's assume it's moved here for now.
export type LocationTuple = [number, number];

export interface Anomaly {
  id: number;
  type: string;
  severity: "low" | "medium" | "high";
  description: string;
  timestamp: string;
  location: LocationTuple;
  resolved: boolean;
  details?: string;
  reportedBy?: string;
  source?: 'api' | 'websocket';
}

interface AnomalyDetailModalProps {
  anomaly: Anomaly | null;
  onClose: () => void;
}

const AnomalyDetailModal: React.FC<AnomalyDetailModalProps> = ({ anomaly, onClose }) => {
  if (!anomaly) return null;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-75 flex items-center justify-center z-50 p-4">
      <div className="bg-matrix-panel p-6 rounded-lg shadow-xl max-w-lg w-full text-matrix border border-matrix-border">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-xl font-bold uppercase">{anomaly.type} - Details</h2>
          {/* Assuming MatrixButton can accept children or a text prop for 'Close' */}
          <MatrixButton onClick={onClose} className="bg-card hover:bg-card/80">Close</MatrixButton>
        </div>
        <div className="space-y-2 text-sm">
          <p><span className="font-semibold">ID:</span> {anomaly.id}</p>
          <p><span className="font-semibold">Severity:</span> <span className={`capitalize font-bold ${
             anomaly.severity === "high" ? "text-destructive" : // Theme-aligned
             anomaly.severity === "medium" ? "text-yellow-500" : // Theme-aligned (standard Tailwind orange/yellow)
             "text-green-500" // Theme-aligned (standard Tailwind green)
          }`}>{anomaly.severity}</span></p>
          <p><span className="font-semibold">Description:</span> {anomaly.description}</p>
          <p><span className="font-semibold">Timestamp:</span> {new Date(anomaly.timestamp).toLocaleString()}</p>
          <p><span className="font-semibold">Location:</span> Lat: {anomaly.location[0].toFixed(5)}, Lon: {anomaly.location[1].toFixed(5)}</p>
          {anomaly.resolved && <p className="font-semibold text-muted-foreground">Status: Resolved</p>}
          {anomaly.details && <p><span className="font-semibold">Additional Details:</span> {anomaly.details}</p>}
          {anomaly.reportedBy && <p><span className="font-semibold">Reported By:</span> {anomaly.reportedBy}</p>}
        </div>
      </div>
    </div>
  );
};

export default AnomalyDetailModal;
