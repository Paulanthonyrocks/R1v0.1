// /home/user/R1v0.1/frontend/app/anomalies/page.tsx
"use client";
// /home/user/R1v0.1/frontend/app/anomalies/page.tsx
"use client";
import React, { useState, useRef, useMemo, useEffect, useCallback } from 'react'; // Added useCallback
import 'leaflet/dist/leaflet.css';
import MatrixCard from "@/components/MatrixCard";
import dynamic from 'next/dynamic';
import MatrixButton from "@/components/MatrixButton";
import { Check, X, AlertTriangle, Sigma, InfoIcon as LucideInfoIcon, Signal, BatteryFull, Clock } from 'lucide-react'; // Import icons, aliased InfoIcon
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from "@/components/ui/dropdown-menu"; // Import DropdownMenu components

import axios from 'axios'; // Keep axios for mutations
// import useSWR from 'swr'; // Remove SWR
// import { AxiosResponse } from 'axios'; // No longer needed for SWR fetcher
import AuthGuard from '@/components/auth/AuthGuard'; // Import AuthGuard
import { UserRole } from '@/lib/auth/roles'; // Import UserRole from correct path
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates'; // Import the hook
import { AlertData, Anomaly, LocationTuple } from '@/lib/types'; // Import Anomaly and LocationTuple from lib/types
import AnomalyDetailModal from '@/components/anomalies/AnomalyDetailModal'; // Anomaly types removed from this import
import { useToast } from '@/components/ui/toast';
import { WS_URL } from '@/lib/hook';

// Modified AnomalyMapProps to include onMarkerClick and activeAnomalyId
interface AnomalyMapProps {
  anomalies: Anomaly[]; // Pass full anomaly objects for more context
  onMarkerClick?: (anomalyId: number) => void;
  activeAnomalyId?: number | null;
}

const Loading = () => (
  <div className="fixed inset-0 bg-matrix-bg flex items-center justify-center z-50 top-16">
    <div className="animate-pulse text-matrix text-2xl tracking-normal">Loading...</div> {/* Added tracking-normal */}
  </div>
);

const DynamicallyLoadedAnomalyMap = dynamic<AnomalyMapProps>(
  () => import('@/components/MapComponent').then(mod => mod.default),
  {
    ssr: false,
    loading: () => (
      <div className="h-[400px] w-full bg-card rounded overflow-hidden flex items-center justify-center text-muted-foreground tracking-normal"> {/* Added tracking-normal */}
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

// Severity to Icon mapping for anomaly cards (using "low", "medium", "high")
// Note: Anomaly type from lib/types uses "low" | "medium" | "high" for severity.
// The mapAlertDataToAnomaly function already maps various inputs to these.
const cardSeverityIconConfig: Record<"low" | "medium" | "high", React.ElementType> = {
  low: LucideInfoIcon, // Or CheckCircle2 if "low" means "good"
  medium: Sigma, // Or AlertTriangle if "medium" implies warning
  high: AlertTriangle, // Or Bomb / XOctagon for more critical "high"
};

// Type guard for location object
function isLatLng(obj: unknown): obj is { latitude: number; longitude: number } {
  return (
    typeof obj === 'object' &&
    obj !== null &&
    typeof (obj as { latitude?: unknown }).latitude === 'number' &&
    typeof (obj as { longitude?: unknown }).longitude === 'number'
  );
}

const mapAlertDataToAnomaly = (alert: AlertData): Anomaly | null => {
  let locationTuple: LocationTuple | undefined;
  if (alert.details?.location && isLatLng(alert.details.location)) {
    locationTuple = [alert.details.location.latitude, alert.details.location.longitude];
  } else if (
    alert.details?.location_tuple &&
    Array.isArray(alert.details.location_tuple) &&
    alert.details.location_tuple.length === 2
  ) {
    locationTuple = alert.details.location_tuple as LocationTuple;
  }
  if (!locationTuple) {
    locationTuple = [0, 0];
  }
  const anomalyId = alert.id ? parseInt(String(alert.id), 10) : Date.now() + Math.random();
  return {
    id: anomalyId,
    type: alert.description || 'Unknown Event',
    severity: ['Critical', 'Warning', 'Anomaly', 'ERROR'].includes(alert.severity) ? 'high' : 'low',
    description: alert.message,
    timestamp: typeof alert.timestamp === 'string' ? alert.timestamp : alert.timestamp.toISOString(),
    location: locationTuple,
    resolved: !!alert.acknowledged,
    details: alert.details ? JSON.stringify(alert.details) : undefined,
    reportedBy: typeof alert.details?.reportedBy === 'string' ? alert.details.reportedBy : 'System',
    source: 'websocket',
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
  const { alerts: wsAlerts, isReady, startWebSocket } = useRealtimeUpdates(WS_URL);
  const { showToast } = useToast();

  const [allAnomalies, setAllAnomalies] = useState<Anomaly[]>([]);
  const [pageLoading, setPageLoading] = useState(true); // Initial loading state

  const [selectedSeverity, setSelectedSeverity] = useState<SeverityFilter>(ALL_SEVERITIES);
  const [sortOrder, setSortOrder] = useState<SortOrder>("newest");
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
    const mappedAnomalies = wsAlerts.map(alert => mapAlertDataToAnomaly(alert)).filter(Boolean) as Anomaly[];
    setAllAnomalies(prevAnomalies => {
        const newAnomaliesMap = new Map(prevAnomalies.map(a => [a.id, a]));
        mappedAnomalies.forEach(a => newAnomaliesMap.set(a.id, a));
        return Array.from(newAnomaliesMap.values());
    });
  }, [wsAlerts]); // Remove allAnomalies from dependency array

  

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
      showToast("Anomaly acknowledged successfully!", "success");
      // UI is optimistically updated. WebSocket broadcast from backend will confirm to other clients.
      // If this client needs to be sure it has the absolute latest from DB (e.g. other fields changed by backend),
      // then a refetch or specific update from a WS message confirming the PATCH would be needed.
      // For now, optimistic update + WS broadcast to others is the flow.
    } catch {
      showToast("Failed to acknowledge anomaly.", "error");
      setAllAnomalies(prev => prev.map(anomaly =>
        anomaly.id === anomalyId ? { ...anomaly, resolved: false } : anomaly
      ));
      console.error("Failed to acknowledge anomaly");
    }
  };
  const handleDismiss = async (anomalyId: number) => {
    const originalAnomalies = [...allAnomalies]; // Store for potential revert
    // Optimistic UI update (local state)
    setAllAnomalies(prev => prev.filter(anomaly => anomaly.id !== anomalyId));
    try {
      // Use the new endpoint
      await axios.delete(`/api/alerts/${anomalyId}`);
      showToast("Anomaly dismissed successfully!", "success");
      // No SWR mutate. UI is optimistically updated.
      // WebSocket broadcast from backend will inform other clients.
    } catch {
      addToast("Failed to dismiss anomaly.", "error");
      setAllAnomalies(originalAnomalies);
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
  // Remove wsError check since it's not provided by the hook
  // If not pageLoading (i.e. isReady was true) but allAnomalies is empty, it will be handled by the "No anomalies" message below.

  return (
    <AuthGuard requiredRole={UserRole.AGENCY}>
      <div className="bg-lcd-bg text-lcd-text font-lcd flex flex-col min-h-screen w-full">
        {/* Status Bar */}
        <header className="flex items-center justify-between px-4 py-1 border-b-2 border-lcd-text">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <div className="flex items-center space-x-2 cursor-pointer">
                <Signal size={20} />
                <span className="font-lcd matrix-glow">ANOMALIES</span>
              </div>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="matrix-card">
              <DropdownMenuItem asChild>
                <a href="/" className="w-full tracking-normal font-lcd matrix-glow">HOME</a>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="/dashboard" className="w-full tracking-normal font-lcd matrix-glow">DASHBOARD</a>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="/dashboard/map" className="w-full tracking-normal font-lcd matrix-glow">LIVE MAP</a>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="/dashboard/logs" className="w-full tracking-normal font-lcd matrix-glow">SYSTEM LOGS</a>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="/dashboard/analytics" className="w-full tracking-normal font-lcd matrix-glow">ANALYTICS</a>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="/dashboard/preferences" className="w-full tracking-normal font-lcd matrix-glow">PREFERENCES</a>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <div className="flex items-center space-x-2">
            <Clock size={20} />
            <span className="font-lcd matrix-glow">{new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
            <BatteryFull size={20} />
          </div>
        </header>
        <div className="p-4">
        <AnomalyDetailModal anomaly={selectedAnomalyForModal} onClose={() => setSelectedAnomalyForModal(null)} />

        <h1 className="text-2xl font-bold mb-4 uppercase text-matrix tracking-normal">Detected Anomalies</h1> {/* Added tracking-normal */}

        {/* Filters and Sorting UI */}
        <div className="mb-6 flex flex-wrap items-center gap-4">
          <div>
            <label htmlFor="severity-filter" className="text-matrix-muted mr-2 tracking-normal">Severity:</label> {/* Added tracking-normal */}
            <select
              id="severity-filter"
              value={selectedSeverity}
              onChange={(e) => setSelectedSeverity(e.target.value as SeverityFilter)}
              className="bg-matrix-panel text-matrix p-2 rounded-md border border-matrix-border focus:ring-primary focus:border-primary" // Changed focus rings
            >
              <option value={ALL_SEVERITIES}>All</option>
              <option value="low">Low</option>
              <option value="medium">Medium</option>
              <option value="high">High</option>
            </select>
          </div>
          <div>
            <label htmlFor="sort-order" className="text-matrix-muted mr-2 tracking-normal">Sort by:</label> {/* Added tracking-normal */}
            <select
              id="sort-order"
              value={sortOrder}
              onChange={(e) => setSortOrder(e.target.value as SortOrder)}
              className="bg-matrix-panel text-matrix p-2 rounded-md border border-matrix-border focus:ring-primary focus:border-primary" // Changed focus rings
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
                  className={`transition-all duration-300 rounded-lg ${highlightedAnomalyId === anomaly.id ? 'ring-2 ring-primary pixel-drop-shadow' : ''} focus-visible:ring-2 focus-visible:ring-primary cursor-pointer`} // Changed ring and shadow
                  onClick={() => handleCardClick(anomaly)} // Open modal on card click
                  onKeyDown={(e: React.KeyboardEvent<HTMLDivElement>) => {
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      handleCardClick(anomaly);
                    }
                  }}
                  role="button"
                  tabIndex={0}
                  aria-label={`View details for ${anomaly.type}: ${anomaly.description.substring(0, 50)}${anomaly.description.length > 50 ? '...' : ''}`}
                >
                  <MatrixCard
                    title={anomaly.type}
                    // colorOverride prop removed
                  >
                    {/* ... card content (same as before) ... */}
                    <div className="flex flex-col gap-1"> {/* Added gap-1 for spacing */}
                      <p className="text-sm mb-1 flex items-center tracking-normal"> {/* Added tracking-normal */}
                        <span className="font-semibold">Severity:</span>
                        {React.createElement(cardSeverityIconConfig[anomaly.severity] || Sigma, { className: "inline mx-1.5 h-4 w-4 text-primary" })} {/* Added icon */}
                        <span className="capitalize font-bold text-primary">{anomaly.severity}</span>
                        {anomaly.resolved && <span className="ml-2 text-muted-foreground">(Resolved)</span>}
                      </p>
                      <p className="text-sm tracking-normal"> {/* Added tracking-normal */}
                        <span className="font-semibold">Description:</span> {anomaly.description}
                      </p>
                      <p className="mt-2 text-xs text-matrix-muted-text tracking-normal"> {/* Added tracking-normal */}
                        <span className="font-semibold">Timestamp:</span> {new Date(anomaly.timestamp).toLocaleString()}
                      </p>
                      <div className="flex justify-end mt-2 space-x-2">
                        {!anomaly.resolved && (
                         <MatrixButton onClick={(e) => { e.stopPropagation(); handleResolve(anomaly.id); }}>
                              <Check className="mr-1.5 h-4 w-4 text-primary-foreground" /> Resolve
                          </MatrixButton>
                        )}
                        <MatrixButton onClick={(e) => { e.stopPropagation(); handleDismiss(anomaly.id); }}>
                          <X className="mr-1.5 h-4 w-4 text-primary-foreground" /> Dismiss
                        </MatrixButton>
                      </div>
                    </div>
                  </MatrixCard>
                </div>
              ))}
            </div>
          </>
        ) : (
          <div className="text-center text-matrix-muted py-10 tracking-normal font-lcd"> {/* Added tracking-normal */}
            {allAnomalies.length === 0 ? "No anomalies detected." : "No anomalies match the current filters."}
          </div>
        )}
        {/* TODO: Form for reporting new anomalies could be triggered here */}
      </div>
    </div>
    </AuthGuard>
 )
};

export default AnomaliesPage;