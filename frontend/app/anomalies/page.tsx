// /home/user/R1v0.1/frontend/app/anomalies/page.tsx
"use client";
import React, { useState, useRef, useMemo } from 'react'; // Added useRef, useMemo
import 'leaflet/dist/leaflet.css';
import MatrixCard from "@/components/MatrixCard";
import dynamic from 'next/dynamic';
import MatrixButton from "@/components/MatrixButton";

import useSWR from 'swr';
import axios, { AxiosResponse } from 'axios';
import { useEffect } from 'react';
import AuthGuard from '@/components/auth/AuthGuard'; // Import AuthGuard

type LocationTuple = [number, number];

// Modified AnomalyMapProps to include onMarkerClick and activeAnomalyId
interface AnomalyMapProps {
  anomalies: Anomaly[]; // Pass full anomaly objects for more context
  onMarkerClick?: (anomalyId: number) => void;
  activeAnomalyId?: number | null;
}

const Loading = () => (
  <div className="fixed inset-0 bg-matrix-bg flex items-center justify-center z-50 top-16">
    <div className="animate-pulse text-matrix text-2xl">Loading...</div>
  </div>
);

const DynamicallyLoadedAnomalyMap = dynamic<AnomalyMapProps>(
  () => import('@/components/MapComponent').then(mod => mod.default),
  {
    ssr: false,
    loading: () => (
      <div className="h-[400px] w-full bg-gray-700 rounded overflow-hidden flex items-center justify-center text-gray-400">
        Loading map...
      </div>
    ),
  }
);

const AnomalyMap: React.FC<AnomalyMapProps> = ({ anomalies, onMarkerClick, activeAnomalyId }) => {
  return <DynamicallyLoadedAnomalyMap anomalies={anomalies} onMarkerClick={onMarkerClick} activeAnomalyId={activeAnomalyId} />;
};

interface Anomaly {
  id: number;
  type: string;
  severity: "low" | "medium" | "high";
  description: string;
  timestamp: string;
  location: LocationTuple;
  resolved: boolean;
  // Add any other relevant fields for the detailed view
  details?: string; // Example: More detailed text
  reportedBy?: string;
}
export type { Anomaly };

const ALL_SEVERITIES = "all";
type SeverityFilter = "low" | "medium" | "high" | typeof ALL_SEVERITIES;
type SortOrder = "newest" | "oldest";

interface ToastMessage {
  id: number;
  message: string;
  type: 'success' | 'error';
}
const ToastContainer: React.FC<{ toasts: ToastMessage[], removeToast: (id: number) => void }> = ({ toasts, removeToast }) => {
  if (!toasts.length) return null;
  return (
    <div className="fixed bottom-4 right-4 z-[100] space-y-2">
      {toasts.map(toast => (
        <div
          key={toast.id}
          className={`p-3 rounded-md shadow-lg text-white ${toast.type === 'success' ? 'bg-green-500' : 'bg-red-500'}`}
          onClick={() => removeToast(toast.id)}
        >
          {toast.message}
        </div>
      ))}
    </div>
  );
};

// --- Anomaly Detail Modal ---
const AnomalyDetailModal: React.FC<{ anomaly: Anomaly | null; onClose: () => void }> = ({ anomaly, onClose }) => {
  if (!anomaly) return null;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-75 flex items-center justify-center z-50 p-4">
      <div className="bg-matrix-panel p-6 rounded-lg shadow-xl max-w-lg w-full text-matrix border border-matrix-border">
        <div className="flex justify-between items-center mb-4">
          <h2 className="text-xl font-bold uppercase">{anomaly.type} - Details</h2>
          <MatrixButton onClick={onClose} color="gray" size="small">Close</MatrixButton>
        </div>
        <div className="space-y-2 text-sm">
          <p><span className="font-semibold">ID:</span> {anomaly.id}</p>
          <p><span className="font-semibold">Severity:</span> <span className={`capitalize font-bold ${
             anomaly.severity === "high" ? "text-red-400" :
             anomaly.severity === "medium" ? "text-orange-400" :
             "text-green-400"
          }`}>{anomaly.severity}</span></p>
          <p><span className="font-semibold">Description:</span> {anomaly.description}</p>
          <p><span className="font-semibold">Timestamp:</span> {new Date(anomaly.timestamp).toLocaleString()}</p>
          <p><span className="font-semibold">Location:</span> Lat: {anomaly.location[0].toFixed(5)}, Lon: {anomaly.location[1].toFixed(5)}</p>
          {anomaly.resolved && <p className="font-semibold text-gray-500">Status: Resolved</p>}
          {anomaly.details && <p><span className="font-semibold">Additional Details:</span> {anomaly.details}</p>}
          {anomaly.reportedBy && <p><span className="font-semibold">Reported By:</span> {anomaly.reportedBy}</p>}
        </div>
        {/* Add action buttons here if needed, e.g., for assignment or further resolution steps */}
      </div>
    </div>
  );
};
// --- End Anomaly Detail Modal ---


const fetcher = (url: string) => axios.get(url).then((res: AxiosResponse<Anomaly[]>) => res.data);

const AnomaliesPage = () => {
  const { data, error, isLoading, mutate } = useSWR<Anomaly[]>('/api/anomalies', fetcher);
  const [selectedSeverity, setSelectedSeverity] = useState<SeverityFilter>(ALL_SEVERITIES);
  const [sortOrder, setSortOrder] = useState<SortOrder>("newest");
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [selectedAnomalyForModal, setSelectedAnomalyForModal] = useState<Anomaly | null>(null);
  const [highlightedAnomalyId, setHighlightedAnomalyId] = useState<number | null>(null);
  const cardRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const [mapId, setMapId] = useState(Date.now());

  const addToast = (message: string, type: 'success' | 'error') => {
    const id = Date.now();
    setToasts(prev => [...prev, { id, message, type }]);
    setTimeout(() => removeToast(id), 3000);
  };
  const removeToast = (id: number) => setToasts(prev => prev.filter(toast => toast.id !== id));

  const allAnomalies: Anomaly[] = data || [];

  const processedAnomalies = allAnomalies
    .filter(anomaly => selectedSeverity === ALL_SEVERITIES || anomaly.severity === selectedSeverity)
    .sort((a, b) => {
      const dateA = new Date(a.timestamp).getTime();
      const dateB = new Date(b.timestamp).getTime();
      return sortOrder === "newest" ? dateB - dateA : dateA - dateB;
    });

  // Create a signature that changes only when relevant anomaly data changes
  const anomaliesSignature = useMemo(() => {
    // Only include anomalies with a valid location in the signature
    return processedAnomalies
      .filter(a => a.location && Array.isArray(a.location) && a.location.length >= 2)
      .map(a => `${a.id}-${a.location[0]}-${a.location[1]}`)
      .join('|');
  }, [processedAnomalies]); // Depend on the processed list

  // Add a key to the map component to force a remount when the anomalies change
  // The key is updated when the anomaliesSignature changes
  useEffect(() => {
    // Update mapId when the list of processed anomalies changes to force map remount
    setMapId(Date.now());
  }, [anomaliesSignature]); // Depend on the signature

  const handleResolve = async (anomalyId: number) => {
    const optimisticData = allAnomalies.map(anomaly =>
      anomaly.id === anomalyId ? { ...anomaly, resolved: true } : anomaly
    );
    mutate(optimisticData, false);
    try {
      await axios.patch(`/api/anomalies/${anomalyId}`, { resolved: true });
      addToast("Anomaly resolved successfully!", "success");
      mutate();
    } catch (err) {
      addToast("Failed to resolve anomaly.", "error");
      mutate();
      console.error("Failed to resolve anomaly", err);
    }
  };
  const handleDismiss = async (anomalyId: number) => {
    const optimisticData = allAnomalies.filter(anomaly => anomaly.id !== anomalyId);
    mutate(optimisticData, false);
    try {
      await axios.delete(`/api/anomalies/${anomalyId}`);
      addToast("Anomaly dismissed.", "success");
      mutate();
    } catch (err) {
      addToast("Failed to dismiss anomaly.", "error");
      mutate();
      console.error("Failed to dismiss anomaly", err);
    }
  };

  const handleMarkerClick = (anomalyId: number) => {
    setHighlightedAnomalyId(anomalyId);
    const cardElement = cardRefs.current[anomalyId];
    if (cardElement) {
      cardElement.scrollIntoView({ behavior: 'smooth', block: 'center' });
      // Remove highlight after a delay
      setTimeout(() => setHighlightedAnomalyId(null), 2000);
    }
    // Optionally, also open the modal:
    // const anomaly = allAnomalies.find(a => a.id === anomalyId);
    // if (anomaly) setSelectedAnomalyForModal(anomaly);
  };

  const handleCardClick = (anomaly: Anomaly) => {
    setSelectedAnomalyForModal(anomaly);
  };


  if (isLoading) return <Loading />;
  if (error) return <div className="p-4 text-red-500">Failed to load anomalies. Please try again later.</div>;

  return (
    <AuthGuard> {/* Wrap content with AuthGuard */}
      <div className="p-4">
        <ToastContainer toasts={toasts} removeToast={removeToast} />
        <AnomalyDetailModal anomaly={selectedAnomalyForModal} onClose={() => setSelectedAnomalyForModal(null)} />

        <h1 className="text-2xl font-bold mb-4 uppercase text-matrix">Detected Anomalies</h1>

        {/* Filters and Sorting UI */}
        <div className="mb-6 flex flex-wrap items-center gap-4">
          <div>
            <label htmlFor="severity-filter" className="text-matrix-muted mr-2">Severity:</label>
            <select
              id="severity-filter"
              value={selectedSeverity}
              onChange={(e) => setSelectedSeverity(e.target.value as SeverityFilter)}
              className="bg-matrix-panel text-matrix p-2 rounded-md border border-matrix-border focus:ring-matrix-green focus:border-matrix-green"
            >
              <option value={ALL_SEVERITIES}>All</option>
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
            </select>
          </div>
          <div>
            <label htmlFor="sort-order" className="text-matrix-muted mr-2">Sort by:</label>
            <select
              id="sort-order"
              value={sortOrder}
              onChange={(e) => setSortOrder(e.target.value as SortOrder)}
              className="bg-matrix-panel text-matrix p-2 rounded-md border border-matrix-border focus:ring-matrix-green focus:border-matrix-green"
            >
              <option value="newest">Newest First</option>
              <option value="oldest">Oldest First</option>
            </select>
          </div>
        </div>

        {/* TODO: Pagination controls could go here if implementing pagination */}

        {processedAnomalies.length > 0 ? (
          <>
            <div className="h-[400px] w-full mb-6 bg-gray-700 rounded overflow-hidden">
              <AnomalyMap
                key={mapId} // Use the mapId state as the key
                anomalies={processedAnomalies}
                onMarkerClick={handleMarkerClick}
                activeAnomalyId={highlightedAnomalyId} // Or pass selectedAnomalyForModal?.id
              />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {processedAnomalies.map((anomaly) => (
                <div
                  key={anomaly.id}
                  ref={el => { cardRefs.current[anomaly.id] = el; }} // Assign ref for scrolling
                  className={`transition-all duration-300 rounded-lg ${highlightedAnomalyId === anomaly.id ? 'ring-2 ring-matrix-green shadow-lg' : ''}`}
                  onClick={() => handleCardClick(anomaly)} // Open modal on card click
                >
                  <MatrixCard
                    title={anomaly.type}
                    colorOverride={anomaly.resolved ? "hsl(0, 0%, 50%)" : anomaly.severity === "high"
                      ? "hsl(0, 100%, 50%)"
                      : anomaly.severity === "medium"
                        ? "hsl(39, 100%, 50%)"
                        : "hsl(120, 100%, 35%)"
                    }
                  >
                    {/* ... card content (same as before) ... */}
                    <div className="flex flex-col">
                      <p className="text-sm mb-1">
                        <span className="font-semibold">Severity:</span> <span className={`capitalize font-bold ${
                           anomaly.severity === "high" ? "text-red-400" :
                           anomaly.severity === "medium" ? "text-orange-400" :
                           "text-green-400"
                        }`}>{anomaly.severity}</span>
                        {anomaly.resolved && <span className="ml-2 text-gray-500">(Resolved)</span>}
                      </p>
                      <p className="text-sm">
                        <span className="font-semibold">Description:</span> {anomaly.description}
                      </p>
                      <p className="mt-2 text-xs text-matrix-muted-text">
                        <span className="font-semibold">Timestamp:</span> {new Date(anomaly.timestamp).toLocaleString()}
                      </p>
                      <div className="flex justify-end mt-2 space-x-2">
                        {!anomaly.resolved && (
                         <MatrixButton onClick={(e) => { e.stopPropagation(); handleResolve(anomaly.id); }} color="green">
                              Resolve
                          </MatrixButton>
                        )}
                        <MatrixButton onClick={(e) => { e.stopPropagation(); handleDismiss(anomaly.id); }} color="red">
                          Dismiss
                        </MatrixButton>
                      </div>
                    </div>
                  </MatrixCard>
                </div>
              ))}
            </div>
          </>
        ) : (
          <div className="text-center text-matrix-muted py-10">
            {allAnomalies.length === 0 ? "No anomalies detected." : "No anomalies match the current filters."}
          </div>
        )}
        {/* TODO: Form for reporting new anomalies could be triggered here */}
      </div>
    </AuthGuard> /* Close AuthGuard */
 )
};

export default AnomaliesPage;