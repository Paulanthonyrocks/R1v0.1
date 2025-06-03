// /home/user/R1v0.1/frontend/app/anomalies/page.tsx
"use client";
// /home/user/R1v0.1/frontend/app/anomalies/page.tsx
"use client";
import React, { useState, useRef, useMemo, useEffect, useCallback } from 'react'; // Added useCallback
import 'leaflet/dist/leaflet.css';
import MatrixCard from "@/components/MatrixCard";
import dynamic from 'next/dynamic';
import MatrixButton from "@/components/MatrixButton";

import axios from 'axios'; // Keep axios for mutations
// import useSWR from 'swr'; // Remove SWR
// import { AxiosResponse } from 'axios'; // No longer needed for SWR fetcher
import AuthGuard from '@/components/auth/AuthGuard'; // Import AuthGuard
import { UserRole } from '@/types/user'; // Import UserRole
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates'; // Import the hook
import { AlertData, Anomaly, LocationTuple } from '@/lib/types'; // Import Anomaly and LocationTuple from lib/types
import AnomalyDetailModal from '@/components/anomalies/AnomalyDetailModal'; // Anomaly types removed from this import
import ToastContainer, { ToastMessage } from '@/components/ui/ToastContainer';


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
      <div className="h-[400px] w-full bg-card rounded overflow-hidden flex items-center justify-center text-muted-foreground">
        Loading map...
      </div>
    ),
  }
);

const AnomalyMap: React.FC<AnomalyMapProps> = ({ anomalies, onMarkerClick, activeAnomalyId }) => {
  return <DynamicallyLoadedAnomalyMap anomalies={anomalies} onMarkerClick={onMarkerClick} activeAnomalyId={activeAnomalyId} />;
};

// Helper to map AlertData from hook to local Anomaly type
// Anomaly and LocationTuple types are now imported from AnomalyDetailModal.tsx
const mapAlertDataToAnomaly = (alert: AlertData, existingAnomalies: Anomaly[] = []): Anomaly | null => {
  // Try to find if this alert (by message and timestamp proximity, or ideally a unique ID from details)
  // corresponds to an already known anomaly from API to preserve its 'resolved' status or DB ID.
  // This is a simplified example; robust matching can be complex.
  // For now, assume new alerts from WebSocket are unresolved unless they have a known ID.

  let locationTuple: LocationTuple | undefined;
  if (alert.details?.location && typeof alert.details.location.latitude === 'number' && typeof alert.details.location.longitude === 'number') {
    locationTuple = [alert.details.location.latitude, alert.details.location.longitude];
  } else if (alert.details?.location_tuple && Array.isArray(alert.details.location_tuple) && alert.details.location_tuple.length === 2) {
    // Fallback if location_tuple is directly provided in details
    locationTuple = alert.details.location_tuple as LocationTuple;
  }


  // If location is crucial and not present, might return null or a default location
  if (!locationTuple) {
      console.warn("Alert data missing valid location, cannot map to Anomaly for map display:", alert);
      // Decide if you want to display alerts without map locations or filter them out
      // For now, let's allow them but they won't show on map if map component requires location.
      // The page's AnomalyMap component filters for valid locations before rendering.
      locationTuple = [0,0]; // Default or skip
  }

  // Use a unique ID if available from alert.id (from WebSocket) or generate one for local state keying if necessary.
  // The backend API uses numeric IDs. WebSocket alerts might have string IDs or require client-side generation for keys.
  // This example assumes `alert.id` from WebSocket is usable as string, local `Anomaly` needs number.
  // This part needs careful handling based on actual ID types from WebSocket and API.
  // For now, let's use a temporary solution for ID:
  const anomalyId = alert.id ? parseInt(alert.id, 10) : Date.now() + Math.random();


  return {
    id: anomalyId,
    type: alert.type || 'Unknown Event',
    // Ensure severity mapping handles all cases, especially if AlertData.severity has more options than Anomaly.severity
    severity: (alert.severity === 'info' || alert.severity === 'Anomaly' || alert.severity === 'Warning' || alert.severity === 'Critical' || alert.severity === 'ERROR')
              ? (alert.severity === 'info' ? 'low' : alert.severity.toLowerCase() as "low" | "medium" | "high")
              : 'low', // Default for unexpected severity values
    description: alert.message,
    timestamp: typeof alert.timestamp === 'string' ? alert.timestamp : alert.timestamp.toISOString(),
    location: locationTuple,
    resolved: !!alert.acknowledged, // Set 'resolved' based on 'acknowledged' status from WebSocket/AlertData
    details: alert.details ? JSON.stringify(alert.details) : undefined,
    reportedBy: alert.details?.reportedBy || 'System',
    source: 'websocket', // Or determine based on how data is fetched/merged if API data is also used
  };
};


const ALL_SEVERITIES = "all";
type SeverityFilter = "low" | "medium" | "high" | typeof ALL_SEVERITIES;
type SortOrder = "newest" | "oldest";

// ToastMessage type is now imported from ToastContainer.tsx
// ToastContainer component is now imported

// AnomalyDetailModal component is now imported
// Anomaly and LocationTuple types are now imported from AnomalyDetailModal.tsx


// const fetcher = (url: string) => axios.get(url).then((res: AxiosResponse<Anomaly[]>) => res.data); // SWR fetcher removed

const AnomaliesPage = () => {
  // const { data, error, isLoading, mutate } = useSWR<Anomaly[]>('/api/anomalies', fetcher); // SWR removed
  const { alerts: wsAlerts, isConnected, isReady, error: wsError, startWebSocket } = useRealtimeUpdates('ws://localhost:9002/ws');

  const [allAnomalies, setAllAnomalies] = useState<Anomaly[]>([]);
  const [pageLoading, setPageLoading] = useState(true); // Initial loading state

  const [selectedSeverity, setSelectedSeverity] = useState<SeverityFilter>(ALL_SEVERITIES);
  const [sortOrder, setSortOrder] = useState<SortOrder>("newest");
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [selectedAnomalyForModal, setSelectedAnomalyForModal] = useState<Anomaly | null>(null);
  const [highlightedAnomalyId, setHighlightedAnomalyId] = useState<number | null>(null);
  const cardRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const [mapId, setMapId] = useState(Date.now());

  useEffect(() => {
    console.log("AnomaliesPage: Attempting to start WebSocket connection.");
    startWebSocket();
  }, [startWebSocket]);

  useEffect(() => {
    // Update loading state based on WebSocket readiness
    if (isReady) {
      setPageLoading(false);
    }
  }, [isReady]);

  useEffect(() => {
    // Map AlertData from WebSocket to local Anomaly type
    // This simple version replaces allAnomalies with mapped wsAlerts.
    // A more complex version might merge or reconcile with data fetched from API or existing state.
    const mappedAnomalies = wsAlerts.map(alert => mapAlertDataToAnomaly(alert, allAnomalies)).filter(Boolean) as Anomaly[];

    // To prevent infinite loops if mapAlertDataToAnomaly is not stable or wsAlerts reference changes too often:
    // Consider deep comparison or more selective updates if performance issues arise.
    // For now, a direct update:
    setAllAnomalies(prevAnomalies => {
        // Simple merge: add new, update existing (by id)
        const newAnomaliesMap = new Map(prevAnomalies.map(a => [a.id, a]));
        mappedAnomalies.forEach(a => newAnomaliesMap.set(a.id, a));
        return Array.from(newAnomaliesMap.values());
    });

  }, [wsAlerts]); // Removed allAnomalies from dependency array to avoid potential loops with naive merge

  const addToast = (message: string, type: 'success' | 'error') => {
    const id = Date.now();
    setToasts(prev => [...prev, { id, message, type }]);
    setTimeout(() => removeToast(id), 3000);
  };
  const removeToast = (id: number) => setToasts(prev => prev.filter(toast => toast.id !== id));

  // const allAnomalies: Anomaly[] = data || []; // Now using state `allAnomalies`

  const processedAnomalies = allAnomalies
    .filter(anomaly => selectedSeverity === ALL_SEVERITIES || anomaly.severity === selectedSeverity)
    .sort((a, b) => {
      // Ensure timestamps are valid dates before comparison
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
  }, [anomaliesSignature]); // Depend on the signature, anomaliesSignature should now use `allAnomalies` state

  const handleResolve = async (anomalyId: number) => {
    // Optimistic UI update (local state)
    setAllAnomalies(prev => prev.map(anomaly =>
      anomaly.id === anomalyId ? { ...anomaly, resolved: true } : anomaly
    ));
    try {
      // Use the new endpoint and request body structure
      await axios.patch(`/api/alerts/${anomalyId}/acknowledge`, { acknowledged: true });
      addToast("Anomaly acknowledged successfully!", "success");
      // UI is optimistically updated. WebSocket broadcast from backend will confirm to other clients.
      // If this client needs to be sure it has the absolute latest from DB (e.g. other fields changed by backend),
      // then a refetch or specific update from a WS message confirming the PATCH would be needed.
      // For now, optimistic update + WS broadcast to others is the flow.
    } catch (err) {
      addToast("Failed to acknowledge anomaly.", "error");
      // Revert optimistic update on error
      setAllAnomalies(prev => prev.map(anomaly =>
        anomaly.id === anomalyId ? { ...anomaly, resolved: false } : anomaly // Assuming it was false before
      ));
      console.error("Failed to acknowledge anomaly", err);
    }
  };
  const handleDismiss = async (anomalyId: number) => {
    const originalAnomalies = [...allAnomalies]; // Store for potential revert
    // Optimistic UI update (local state)
    setAllAnomalies(prev => prev.filter(anomaly => anomaly.id !== anomalyId));
    try {
      // Use the new endpoint
      await axios.delete(`/api/alerts/${anomalyId}`);
      addToast("Anomaly dismissed successfully.", "success");
      // No SWR mutate. UI is optimistically updated.
      // WebSocket broadcast from backend will inform other clients.
    } catch (err) {
      addToast("Failed to dismiss anomaly.", "error");
      setAllAnomalies(originalAnomalies); // Revert on error
      // if WebSocket doesn't reflect this specific state change.
    // } catch (err) { // This catch seems to be a duplicate or misplaced part of the original handleResolve
    //   addToast("Failed to resolve anomaly.", "error");
    //   // Revert optimistic update on error
    //   setAllAnomalies(prev => prev.map(anomaly =>
    //     anomaly.id === anomalyId ? { ...anomaly, resolved: false } : anomaly // Assuming it was false before
    //   ));
    //   console.error("Failed to resolve anomaly", err);
    }
  };
  // const handleDismiss = async (anomalyId: number) => { // Removing duplicate
  //   const originalAnomalies = [...allAnomalies];
  //   setAllAnomalies(prev => prev.filter(anomaly => anomaly.id !== anomalyId));
  //   try {
  //     await axios.delete(`/api/anomalies/${anomalyId}`); // This uses /api/anomalies path
  //     addToast("Anomaly dismissed.", "success");
  //   } catch (err) {
  //     addToast("Failed to dismiss anomaly.", "error");
  //     setAllAnomalies(originalAnomalies);
  //     console.error("Failed to dismiss anomaly", err);
  //   }
  // };

  const handleMarkerClick = useCallback((anomalyId: number) => {
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
  }, []); // cardRefs.current is stable, setHighlightedAnomalyId is stable from useState

  const handleCardClick = (anomaly: Anomaly) => {
    setSelectedAnomalyForModal(anomaly);
  };

  // Updated loading and error states
  if (pageLoading && !isReady) return <Loading />; // Show main loading if not ready from WebSocket
  if (wsError) return <div className="p-4 text-red-500">Error connecting to real-time updates: {String(wsError.message || wsError)}. Please try again later.</div>;
  // If not pageLoading (i.e. isReady was true) but allAnomalies is empty, it will be handled by the "No anomalies" message below.

  return (
    <AuthGuard requiredRole={UserRole.AGENCY}>
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
                           anomaly.severity === "high" ? "text-destructive" :
                           anomaly.severity === "medium" ? "text-yellow-500" :
                           "text-green-500"
                        }`}>{anomaly.severity}</span>
                        {anomaly.resolved && <span className="ml-2 text-muted-foreground">(Resolved)</span>}
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
    </AuthGuard>
 )
};

export default AnomaliesPage;