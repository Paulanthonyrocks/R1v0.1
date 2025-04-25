// app/page.tsx
"use client";

import React, { useState, useEffect, useMemo } from 'react';
// Note: Error 7016 about missing 'react' declaration is an environment/setup issue.
// Ensure @types/react is installed (`npm i --save-dev @types/react`).
import useSWR from 'swr';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Plus, MapPin, AlertTriangle } from 'lucide-react';
import { cn } from "@/lib/utils";
import { List } from 'react-virtualized'; // Import List

// Import dashboard components (assuming index export or direct paths)
import {
    StatCard, StatCardSkeleton, AnomalyItem, CongestionNode,
    CongestionNodeSkeleton, SurveillanceFeed, SurveillanceFeedSkeleton,
    LegendItem, PageLayout
} from '@/components/dashboard'; // Adjust path if needed
import FlowAnalysisChart from '@/components/dashboard/FlowAnalysisChart';
import AnomalyDetailsModal from '@/components/dashboard/AnomalyDetailsModal';
import ReportAnomalyModal from '@/components/dashboard/ReportAnomalyModal';
import ErrorBoundary from '@/components/ErrorBoundary'; // Import ErrorBoundary

// Import types
import { StatCardData, AlertData, FeedStatusData, TrendDataPoint, AlertsResponse, SeverityLevel, KpiData } from '@/lib/types'; // Add KpiData type

// Import the WebSocket client instance
import { ws } from '@/lib/websocket';

// Import mock data only for fallbacks/placeholders
import { mockStatCards, mockCongestionNodes } from '@/data/mockData'; // Add mockSurveillanceFeeds

// --- SWR Fetcher Function ---
const fetcher = async (url: string) => {
    const res = await fetch(url);
    if (!res.ok) {
        const contentType = res.headers.get('content-type');
        let errorMessage = 'Failed to fetch data';
        
        try {
            if (contentType?.includes('application/json')) {
                // Try parsing JSON error details from backend
                const json = await res.json();
                errorMessage = json.detail || json.message || errorMessage;
            } else {
                // For non-JSON responses (like HTML), use status text
                errorMessage = `Server error: ${res.status} ${res.statusText}`;
            }
        } catch {
            errorMessage = `Server error: ${res.status} ${res.statusText}`;
        }
        
        throw new Error(errorMessage);
    }
    return res.json();
};

// Base API URL
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api/v1';

// --- Main Page Component ---
export default function DashboardPage() {
    // --- State Management ---
    // State for WebSocket connection status
    const [isConnected, setIsConnected] = useState(false);
    const [wsError, setWsError] = useState<string | null>(null);

    // State for real-time data from WebSocket
    const [kpiData, setKpiData] = useState<KpiData | null>(null);
    const [liveFeeds, setLiveFeeds] = useState<FeedStatusData[]>([]);
    const [liveAlerts, setLiveAlerts] = useState<AlertData[]>([]);

    // State for Chart Time Range Selection
    const [timeRange, setTimeRange] = useState<'day' | 'week' | 'month'>('week'); // Default to week

    // State for Modals
    const [selectedAnomaly, setSelectedAnomaly] = useState<AlertData | null>(null);
    const [isReportModalOpen, setIsReportModalOpen] = useState(false);

    // --- SWR Data Fetching ---
    // Fetch initial feeds status
    const { data: initialFeedsData, error: feedsError, isLoading: isLoadingFeeds } = useSWR<FeedStatusData[]>(
        `${API_BASE_URL}/feeds`, fetcher, { revalidateOnFocus: false }
    );

    // State for current page of alerts
    const [currentPage, setCurrentPage] = useState(1);

    // Fetch initial page of alerts
    const {
        data: initialAlertsResponse,
        error: alertsError,
        isLoading: isLoadingAlerts,
        mutate: mutateAlerts,
    } = useSWR<AlertsResponse>(`${API_BASE_URL}/alerts?limit=50&page=${currentPage}`, fetcher, { revalidateOnFocus: false });

    // SWR key calculation for Trends Data based on timeRange state
    const trendsSWRKey = useMemo(() => {
         const baseNow = new Date();
         const now = new Date(baseNow); // Work with a copy
         let startTime: Date;

         switch(timeRange) {
             case 'day':
                 startTime = new Date(baseNow.getTime() - 24 * 60 * 60 * 1000);
                 break;
             case 'month':
                 startTime = new Date(baseNow.getFullYear(), baseNow.getMonth() - 1, baseNow.getDate(), baseNow.getHours(), baseNow.getMinutes(), baseNow.getSeconds());
                 break;
             case 'week':
             default:
                 startTime = new Date(baseNow.getTime() - 7 * 24 * 60 * 60 * 1000);
                 break;
         }
         const endTimeStr = now.toISOString();
         const startTimeStr = startTime.toISOString();
         return `${API_BASE_URL}/analysis/trends?start_time=${startTimeStr}&end_time=${endTimeStr}`;
    }, [timeRange]);

    // Fetch trends data using the dynamic key
    const { data: trendsData, error: trendsError, isLoading: isLoadingTrends } = useSWR<TrendDataPoint[]>(
        trendsSWRKey,
        fetcher,
        { revalidateOnFocus: false }
    );

    // --- WebSocket Connection and Subscription ---
    useEffect(() => {
        let isMounted = true;

        const connectWebSocket = async () => {
            try {
                await ws.connect();
                if (isMounted) {
                    setIsConnected(true);
                    setWsError(null);
                    console.log("WebSocket connected successfully.");
                }
            } catch (error) {
                console.error("WebSocket connection failed:", error);
                if (isMounted) {
                    setIsConnected(false);
                    setWsError("Failed to connect to real-time updates.");
                }
            }
        };

        connectWebSocket();

        // Define listeners
        const handleKpiUpdate = (data: KpiData) => {
            console.log('Received kpi_update:', data);
            if (isMounted) setKpiData(data);
        };
        const handleFeedUpdate = (data: FeedStatusData) => {
            console.log('Received feed_update:', data);
            if (isMounted) {
                setLiveFeeds(prevFeeds => {
                    const index = prevFeeds.findIndex(f => f.id === data.id);
                    if (index !== -1) {
                        // Update existing feed
                        const updatedFeeds = [...prevFeeds];
                        updatedFeeds[index] = data;
                        return updatedFeeds;
                    } else {
                        // Add new feed (should ideally come from initial load first)
                        return [...prevFeeds, data];
                    }
                });
            }
        };
        const handleNewAlert = (data: AlertData) => {
            console.log('Received new_alert:', data);
             if (isMounted) {
                // Add to the beginning of the list and potentially limit size
                setLiveAlerts(prevAlerts => [data, ...prevAlerts].slice(0, 100)); // Keep latest 100
             }
        };

        // Subscribe
        ws.subscribe('kpi_update', handleKpiUpdate);
        ws.subscribe('feed_update', handleFeedUpdate);
        ws.subscribe('new_alert', handleNewAlert);

        // Cleanup on unmount
        return () => {
            isMounted = false;
            ws.unsubscribe('kpi_update', handleKpiUpdate);
            ws.unsubscribe('feed_update', handleFeedUpdate);
            ws.unsubscribe('new_alert', handleNewAlert);
            ws.disconnect();
            console.log("WebSocket disconnected.");
        };
    }, []); // Empty dependency array ensures this runs only once on mount

    // --- State Synchronization (Load initial data into live state) ---
    useEffect(() => {
        if (initialFeedsData) {
            setLiveFeeds(initialFeedsData);
        }
    }, [initialFeedsData]);

    useEffect(() => {
        if (initialAlertsResponse?.alerts) {
            setLiveAlerts(initialAlertsResponse.alerts);
        }
    }, [initialAlertsResponse]);

    // --- Loading & Error State Calculation ---
    const isInitialLoading = isLoadingFeeds || isLoadingAlerts || isLoadingTrends;
    const fetchError = feedsError || alertsError || trendsError;
    const errorMessage = wsError ? (isConnected ? 'Real-time updates may be interrupted.' : wsError) : (fetchError ? `Failed to load initial data: ${fetchError.message}` : null);


    // --- Data Preparation for Rendering ---
    // Use live data if available, otherwise initial data
    const displayFeeds = liveFeeds.length > 0 ? liveFeeds : initialFeedsData || [];
    const displayAlerts = liveAlerts.length > 0 ? liveAlerts : initialAlertsResponse?.alerts || [];

    // Detect if we're using sample feed
    const isUsingSampleFeed = displayFeeds.length === 1 && displayFeeds[0]?.source?.includes('sample_traffic.mp4');

    // Banner component for sample feed notice will be rendered in the return statement
    const sampleFeedBanner = isUsingSampleFeed && (
        <div className="p-4 bg-info/10 text-info-foreground border border-info rounded-md flex items-center gap-3 mb-4">
            <AlertTriangle className="h-5 w-5 flex-shrink-0" />
            <div>
                <p className="font-semibold">Demo Mode</p>
                <p className="text-sm">Currently displaying sample feed data. Add a real feed to see live traffic analysis.</p>
            </div>
        </div>
    );

    // Map KPIs from WebSocket state (kpiData) to StatCard data
    const kpiStatCards: StatCardData[] = kpiData ? [
        {
            id: 'stat1',
            title: "Total Flow", // Placeholder - KPI data doesn't have total flow yet
            value: String(kpiData.feed_status_counts?.running ?? 'N/A'), // Example: Use running feeds count
            change: isUsingSampleFeed ? "Sample Feed" : "",
            changeText: isUsingSampleFeed ? "Demo Data" : "Real-time",
            icon: mockStatCards[0].icon,
            valueColor: mockStatCards[0].valueColor ?? ''
        },
        {
            id: 'stat2',
            title: "Active Alerts",
            value: String(kpiData.active_incidents ?? displayAlerts.length), // Use KPI data if available
            change: isUsingSampleFeed ? "Sample Feed" : "",
            changeText: isUsingSampleFeed ? "Demo Data" : "Real-time",
            icon: mockStatCards[1].icon,
            valueColor: mockStatCards[1].valueColor ?? ''
        },
        {
            id: 'stat3',
            title: "Avg. Speed",
            value: `${kpiData.avg_speed?.toFixed(1) ?? 'N/A'} mph`,
            change: isUsingSampleFeed ? "Sample Feed" : "",
            changeText: isUsingSampleFeed ? "Demo Data" : "Real-time",
            icon: mockStatCards[2].icon,
            valueColor: mockStatCards[2].valueColor ?? ''
        },
        {
            id: 'stat4',
            title: "Congestion",
            value: `${kpiData.congestion_index?.toFixed(1) ?? 'N/A'} %`,
            change: isUsingSampleFeed ? "Sample Feed" : "",
            changeText: isUsingSampleFeed ? "Demo Data" : "Real-time",
            icon: mockStatCards[3].icon,
            valueColor: kpiData.congestion_index > 50 ? 'text-amber-400' : 'text-green-500'
        },
    ] : Array.from({ length: 4 }).map((_, i) => ({ // Loading state
        ...mockStatCards[i],
        value: '...',
        change: '',
        changeText: 'Connecting...'
    }));

    // --- Event Handlers ---
    const handleAnomalySelect = (alert: AlertData) => {
        setSelectedAnomaly(alert); // Set selected alert to open the details modal
    };

    const handleReportSubmit = (data: { message: string; severity: SeverityLevel; description?: string; location?: string }) => {
        console.log('Submitting anomaly report via API (placeholder):', data);
        // TODO: Implement POST request to /api/v1/alerts when endpoint is available
        // Example:
        // fetch(`${API_BASE_URL}/alerts`, {
        //     method: 'POST',
        //     headers: { 'Content-Type': 'application/json' },
        //     body: JSON.stringify(data),
        // })
        // .then(res => { if (!res.ok) throw new Error('Failed to submit'); return res.json(); })
        // .then(newAlert => { /* Optionally add to local state or wait for WS update */ })
        // .catch(err => console.error("Report submission failed:", err));
    };

    const handleAcknowledge = (alert: AlertData) => {
        console.log('Acknowledging anomaly via API (placeholder):', alert.id || alert.timestamp);
        // TODO: Implement PATCH request to /api/v1/alerts/{alert.id}/acknowledge when endpoint is available
        // Example:
        // fetch(`${API_BASE_URL}/alerts/${alert.id}/acknowledge`, { method: 'PATCH' })
        // .then(res => { if (!res.ok) throw new Error('Failed to acknowledge'); })
        // .then(() => { /* Optionally update local state or wait for WS */ })
        // .catch(err => console.error("Acknowledge failed:", err));

        // Example optimistic update (remove alert locally immediately)
        // setInitialAlerts(prev => prev.filter(a => a.id !== alert.id && a.timestamp !== alert.timestamp));
    };

    // Event handlers for pagination
    const handleNextPage = () => {
        if (initialAlertsResponse?.total_pages && currentPage < initialAlertsResponse.total_pages) {
            setCurrentPage(currentPage + 1);
            mutateAlerts(); // Trigger re-fetch
        }
    };

    const handlePreviousPage = () => {
        if (currentPage > 1) {
            setCurrentPage(currentPage - 1);
            mutateAlerts(); // Trigger re-fetch
        }
    };


    // --- Render Logic ---
    // Note: Errors like 7026 (JSX element implicitly has type 'any') usually indicate
    // a missing @types/react or incorrect TS/JSX setup, not an error in the code itself.
    return (
        <ErrorBoundary fallbackClassName="m-4 sm:m-6 lg:m-8" componentName="DashboardPage">
            <div className="relative flex flex-col gap-6 md:gap-8 px-4 sm:px-6 lg:px-8">
                {sampleFeedBanner}

                {/* Connection / Fetch Error Banner */}
                {errorMessage && (
                    <div className="p-4 bg-destructive/10 text-destructive-foreground border border-destructive rounded-md flex items-center gap-3 mb-0">
                       <AlertTriangle className="h-5 w-5 flex-shrink-0" />
                       <div>
                           <p className="font-semibold">Warning</p>
                           <p className="text-sm">{errorMessage}</p>
                       </div>
                    </div>
                )}

                {/* Section 1: Stats Cards (KPIs) */}
                <ErrorBoundary componentName="StatsSection">
                    <PageLayout className="grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 sm:gap-6">
                        {(isInitialLoading && !kpiData) // Show skeletons only during initial load AND before first KPI update
                            ? Array.from({ length: 4 }).map((_, index) => <StatCardSkeleton key={index} />)
                            : (kpiStatCards.map((stat, index) => {
                                    // Ensure all required props are present, fallback to mock data if necessary
                                    const mockStat = mockStatCards[index];
                                    return (
                                        <StatCard
                                            key={stat.id}
                                            id={stat.id}
                                            title={stat.title}
                                            value={stat.value}
                                            change={stat.change ?? ''} // Use data from kpiData
                                            changeText={stat.changeText ?? ''} // Use data from kpiData
                                            icon={stat.icon ?? mockStat.icon} // Fallback icon
                                            valueColor={stat.valueColor} // Use calculated color
                                        />
                                    );
                                }))
                        }
                    </PageLayout>
                </ErrorBoundary>

                {/* Section 2: Map and Anomalies */}
                <ErrorBoundary componentName="MapAndAnomaliesSection">
                    {/* Updated section grid definition */}
                    <section className="grid grid-cols-1 md:grid-cols-3 lg:grid-cols-3 gap-6 md:gap-8">
                        {/* Traffic Grid Card - Span adjusted for md breakpoint */}
                        <Card className="md:col-span-2 lg:col-span-2 matrix-glow-card">
                            <CardHeader className="border-b border-border p-4">
                                <CardTitle className="matrix-text-glow uppercase text-base font-semibold tracking-wider">
                                    Traffic <span className="text-muted-foreground font-medium">Grid</span>
                                </CardTitle>
                            </CardHeader>
                            <CardContent className="p-0">
                                {/* Map Grid - Responsive Height */}
                                <div className="map-grid h-64 sm:h-80 md:h-96 relative">
                                    {isLoadingFeeds ? ( // Show map loading only during initial feed fetch
                                        <div className="absolute inset-0 flex items-center justify-center bg-muted/20 animate-pulse">
                                            <MapPin className="w-8 h-8 text-muted-foreground/50 mr-2" />
                                            <span className="text-muted-foreground/80">Loading Map Data...</span>
                                        </div>
                                    ) : (
                                        <div className="absolute inset-0 flex items-center justify-center">
                                            <p className="text-sm text-muted-foreground opacity-75">[Map Component Placeholder]</p>
                                        </div>
                                    )}
                                </div>
                                {/* Map Footer with Report Button */}
                                <div className="p-4 border-t border-border flex flex-wrap justify-between items-center gap-4">
                                    <div className="flex flex-wrap gap-x-4 gap-y-2">
                                        <LegendItem color="bg-green-500" text="Normal" />
                                        <LegendItem color="bg-amber-400" text="Slow" />
                                        <LegendItem color="bg-red-500" text="Congested" />
                                        <LegendItem color="bg-purple-500" text="Anomaly" />
                                    </div>
                                    <Button
                                        size="sm"
                                        className="bg-primary text-primary-foreground hover:bg-matrix-light text-xs px-3 h-8"
                                        onClick={() => setIsReportModalOpen(true)} // Opens the Report Anomaly modal
                                    >
                                        <Plus className="mr-1.5 h-4 w-4" /> Report Anomaly
                                    </Button>
                                </div>
                            </CardContent>
                        </Card>

                        {/* Active Anomalies Card - Responsive Height */}
                        <Card className="matrix-glow-card max-w-md self-start">
                            <CardHeader className="border-b border-border p-4">
                                <CardTitle className="matrix-text-glow uppercase text-base font-semibold tracking-wider">
                                    Active <span className="text-muted-foreground font-medium">Anomalies</span>
                                </CardTitle>
                            </CardHeader>
                            {/* Anomalies Content - Adjusted max-h */}
                            <CardContent className="p-0 divide-y divide-secondary max-h-[50vh] sm:max-h-[480px] overflow-y-auto">
                                {isLoadingAlerts && liveAlerts.length === 0 ? (
                                    <div className="p-6 text-center text-muted-foreground animate-pulse">Loading Anomalies...</div>
                                ) : displayAlerts.length === 0 ? (
                                    <div className="p-6 text-center text-muted-foreground">No active anomalies reported.</div>
                                ) : (
                                    <div>
                                        <List
                                            rowCount={displayAlerts.length}
                                            rowHeight={80} // Adjust as needed
                                            width={350} // Adjust width based on Card size
                                            height={300} // Adjust height based on Card size
                                            rowRenderer={({ key, index, style }) => {
                                                const alert = displayAlerts[index];
                                                return (
                                                    <div key={key} style={style}>
                                                        <AnomalyItem
                                                            {...alert}
                                                            onSelect={handleAnomalySelect}
                                                        />
                                                    </div>
                                                );
                                            }}
                                        />
                                        <div className="flex justify-between mt-4">
                                            <Button
                                                onClick={handlePreviousPage}
                                                disabled={currentPage === 1}
                                                variant="outline"
                                                size="sm"
                                            >
                                                Previous Page
                                            </Button>
                                            <Button
                                                onClick={handleNextPage}
                                                disabled={!initialAlertsResponse?.total_pages || currentPage === initialAlertsResponse.total_pages}
                                                variant="outline"
                                                size="sm"
                                            >
                                                Next Page
                                            </Button>
                                        </div>
                                    </div>
                                )}
                            </CardContent>
                        </Card>
                    </section>
                </ErrorBoundary>

                {/* Section 3: Charts */}
                <ErrorBoundary componentName="ChartsSection">
                    <PageLayout className="lg:grid-cols-2 gap-6 md:gap-8"> {/* Keep consistent gap */}
                        {/* Flow Analysis Card - Responsive Height */}
                        <Card className="matrix-glow-card max-w-xl self-start w-full">
                             <CardHeader className="p-4">
                                 <div className="flex flex-wrap justify-between items-center gap-2 mb-4">
                                     <CardTitle className="matrix-text-glow uppercase text-base font-semibold tracking-wider">Flow <span className="text-muted-foreground font-medium">Analysis</span></CardTitle>
                                     {/* Time Range Buttons */}
                                     <div className="flex space-x-1">
                                         <Button
                                             variant={timeRange === 'day' ? 'default' : 'secondary'}
                                             size="sm"
                                             className={cn("text-xs px-3 h-7", timeRange === 'day' && 'bg-primary text-primary-foreground hover:bg-matrix-light')}
                                             onClick={() => setTimeRange('day')}
                                             disabled={isLoadingTrends}
                                         >Day</Button>
                                         <Button
                                             variant={timeRange === 'week' ? 'default' : 'secondary'}
                                             size="sm"
                                             className={cn("text-xs px-3 h-7", timeRange === 'week' && 'bg-primary text-primary-foreground hover:bg-matrix-light')}
                                             onClick={() => setTimeRange('week')}
                                             disabled={isLoadingTrends}
                                         >Week</Button>
                                         <Button
                                              variant={timeRange === 'month' ? 'default' : 'secondary'}
                                              size="sm"
                                              className={cn("text-xs px-3 h-7", timeRange === 'month' && 'bg-primary text-primary-foreground hover:bg-matrix-light')}
                                              onClick={() => setTimeRange('month')}
                                              disabled={isLoadingTrends}
                                         >Month</Button>
                                     </div>
                                 </div>
                             </CardHeader>
                            {/* Chart Content - Adjusted height */}
                            <CardContent className="h-[40vh] sm:h-64 md:h-72">
                                <FlowAnalysisChart
                                    data={trendsData || []}
                                    isLoading={isLoadingTrends}
                                    timeRange={timeRange}
                                />
                            </CardContent>
                        </Card>

                        {/* Congestion Nodes Card - Updated with disclaimer */}
                        <Card className="matrix-glow-card max-w-xl self-start w-full">
                             <CardHeader className="p-4">
                                 <div className="flex justify-between items-center mb-4">
                                    <CardTitle className="matrix-text-glow uppercase text-base font-semibold tracking-wider">Congestion <span className="text-muted-foreground font-medium">Nodes</span></CardTitle>
                                    <Button variant="link" className="text-primary hover:underline h-auto p-0 text-xs" disabled> {/* Disabled button */}
                                        View All
                                    </Button>
                                 </div>
                             </CardHeader>
                             <CardContent className="space-y-4">
                                 {isInitialLoading // Show generic loading during initial page load
                                     ? Array.from({ length: 4 }).map((_, index) => <CongestionNodeSkeleton key={index} />)
                                     : (
                                         <>

                                             {/* Disclaimer for mock data */}
                                             <div className="text-xs text-muted-foreground mb-2 border-l-2 border-amber-500 pl-2">
                                                 Displaying mock data - backend integration pending.
                                             </div>
                                             {/* Render using updated CongestionNode component */}
                                             {mockCongestionNodes.map((node) => (
                                                 <CongestionNode key={node.id} {...node} />
                                             ))}
                                         </>
                                      )
                                 }
                             </CardContent>
                        </Card>
                    </PageLayout>
                </ErrorBoundary>

                {/* Section 4: Surveillance Feeds - Added responsive grid/gap */}
                <ErrorBoundary componentName="FeedsSection">
                    <PageLayout title="Surveillance Feeds" className="grid-cols-1 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4 sm:gap-6">
                        {isLoadingFeeds && liveFeeds.length === 0
                            ? Array.from({ length: 4 }).map((_, index) => <SurveillanceFeedSkeleton key={index} />)
                            : displayFeeds.length === 0
                                ? <div className="col-span-full text-center text-muted-foreground p-4">No active feeds found.</div>
                                : displayFeeds.map((feed) => (
                                    <SurveillanceFeed
                                        key={feed.id}
                                        id={feed.id}
                                        name={feed.name || feed.source}
                                        node={feed.id} // Assuming node is same as id for now
                                        status={feed.status || 'unknown'}
                                    />
                                ))
                        }
                    </PageLayout>
                </ErrorBoundary>

                {/* --- Render Modals --- */}
                {/* Anomaly Details Modal */}
                {selectedAnomaly && (
                    <AnomalyDetailsModal
                        anomaly={selectedAnomaly}
                        open={selectedAnomaly !== null}
                        onOpenChange={(isOpen) => {
                            if (!isOpen) {
                                setSelectedAnomaly(null);
                            }
                        }}
                        onAcknowledge={handleAcknowledge}
                    />
                )}

                {/* Report Anomaly Modal */}
                <ReportAnomalyModal
                    open={isReportModalOpen}
                    onOpenChange={setIsReportModalOpen} // Handler to open/close
                    onSubmit={handleReportSubmit} // Pass submit handler
                />
                {/* --- END MODALS --- */}

            </div>
        </ErrorBoundary>
    );
}