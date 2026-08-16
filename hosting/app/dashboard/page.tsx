'use client';

import React, { useEffect, useState, useMemo, useRef } from 'react';
import Link from 'next/link';
import AuthGuard from "@/components/auth/AuthGuard";
import { Activity, Zap, AlertTriangle, Users, TrendingDown, TrendingUp, CheckCircle2, ShieldCheck, ChevronRight, Loader2, BarChart2, Terminal, Cpu, Globe } from 'lucide-react';
import { UserRole } from "@/lib/auth/roles";
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import { useVehicleTracking } from '@/lib/hooks/useVehicleTracking';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { formatFeedName } from '@/lib/formatters';

import AnomalyItem from '@/components/dashboard/AnomalyItem';
import StatCard from '@/components/dashboard/StatCard';
import SurveillanceFeed from '@/components/dashboard/SurveillanceFeed';
import FlowAnalysisChart from '@/components/dashboard/FlowAnalysisChart';
import AnomalyDetailsModal from '@/components/dashboard/AnomalyDetailsModal';
import DashboardShell from '@/components/dashboard/DashboardShell';
import { TrendDataPoint, AlertData } from '@/lib/types';
import LaneAnalysisWidget from '@/components/dashboard/LaneAnalysisWidget';
import { analyticsService } from '@/lib/services/analyticsService';
import EcoStatsWidget from '@/components/dashboard/EcoStatsWidget';
import IncidentCommandCenter from '@/components/dashboard/IncidentCommandCenter';

const DashboardPage: React.FC = () => {
  const { kpis, alerts, feeds, isConnected, isReady } = useRealtimeUpdates();
  const { vehicles, version: vehicleVersion } = useVehicleTracking();
  const [activeTab, setActiveTab] = useState<'overview' | 'incidents'>('overview');
  const [kpiHistory, setKpiHistory] = useState<TrendDataPoint[]>([]);
  const [selectedAnomaly, setSelectedAnomaly] = useState<AlertData | null>(null);
  const [isAnomalyModalOpen, setIsAnomalyModalOpen] = useState(false);
  const [isDemoMode, setIsDemoMode] = useState(false);
  const [showLazyWidgets, setShowLazyWidgets] = useState(false);
  const hasAttemptedHistoryLoad = useRef(false);
  const feedRefs = useRef<Map<string, HTMLDivElement>>(new Map());

  const handleAnomalySelect = (alert: AlertData) => {
    setSelectedAnomaly(alert);
    setIsAnomalyModalOpen(true);
  };

  const handleJumpToFeed = (feedId: string) => {
    const element = feedRefs.current.get(feedId);
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'center' });
      element.classList.add('matrix-highlight-flash');
      setTimeout(() => element.classList.remove('matrix-highlight-flash'), 2000);
    }
  };

  useEffect(() => {
    if (kpis) {
      setKpiHistory(prev => {
        // No client-side re-smoothing: the backend already EMA-windows the
        // per-feed metrics (metrics_averaging_window_seconds) and averages the
        // live KPIs over metrics_recent_window_seconds, so a second alpha
        // filter here only added lag to an already-lagged signal (the old
        // alpha=0.4 smoothing made the trend chart look pinned/flat).
        const currentCongestion = kpis.congestion_index ?? 0;
        const currentSpeed = kpis.average_speed_kmh ?? 0;
        
        const newPoint: TrendDataPoint = {
          timestamp: new Date().toISOString(),
          total_vehicles: kpis.total_flow ?? 0,
          avg_speed: Math.round(currentSpeed * 10) / 10,
          congestion_index: Math.round(currentCongestion * 10) / 10,
          health_score: kpis.global_health_score ?? 100
        };

        const updated = [...prev, newPoint];
        const limit = 120;  // Fixed cap: 2 min at 60 update/sec, enough for visible trend
        if (updated.length > limit) {
          return updated.slice(updated.length - limit);
        }
        return updated;
      });
    }
  }, [kpis]);

  useEffect(() => {
    const loadHistory = async () => {
      const targetFeed = feeds.find(f => f.status === 'running') || feeds[0];
      if (!targetFeed || hasAttemptedHistoryLoad.current) return;

      hasAttemptedHistoryLoad.current = true;
      try {
        const history = await analyticsService.getFeedHistory(targetFeed.feed_id, 24);
        if (history && history.length > 0) {
          const mappedHistory: TrendDataPoint[] = history.map(h => ({
            timestamp: h.timestamp,
            total_vehicles: h.vehicle_count,
            avg_speed: h.average_speed,
            congestion_index: h.congestion_score || 0
          }));

          setKpiHistory(prev => {
            if (prev.length === 0) return mappedHistory;
            return prev;
          });
        }
      } catch (err) {
        console.error("Failed to load feed history:", err);
        setTimeout(() => {
            hasAttemptedHistoryLoad.current = false;
        }, 30000);
      }
    };

    if (isReady && feeds.length > 0 && kpiHistory.length === 0) {
      loadHistory();
    }
  }, [isReady, feeds, kpiHistory.length]);

  // Lazy-load non-critical widgets after dashboard is ready
  useEffect(() => {
    if (isReady && activeTab === 'overview') {
      const timer = setTimeout(() => setShowLazyWidgets(true), 500);
      return () => clearTimeout(timer);
    }
    if (activeTab !== 'overview') setShowLazyWidgets(false);
  }, [isReady, activeTab]);

  const calculateTrend = (history: TrendDataPoint[], key: keyof TrendDataPoint) => {
    if (history.length < 5) return { change: 'N/A', text: 'Insufficient data' };
    const recent = history[history.length - 1][key] as number;
    const previous = history[history.length - 5][key] as number;
    if (previous === 0) return { change: '+0%', text: 'Stable' };
    const diff = ((recent - previous) / previous) * 100;
    const sign = diff >= 0 ? '+' : '';
    return {
      change: `${sign}${diff.toFixed(1)}%`,
      text: `Compared to 5 updates ago`
    };
  };

  const congestionTrend = useMemo(() => calculateTrend(kpiHistory, 'congestion_index'), [kpiHistory]);
  const speedTrend = useMemo(() => calculateTrend(kpiHistory, 'avg_speed'), [kpiHistory]);
  const flowTrend = useMemo(() => calculateTrend(kpiHistory, 'total_vehicles'), [kpiHistory]);

  const getCongestionStatusIcon = (val: number | undefined) => {
    if (val === undefined || val === null) return undefined;
    if (val > 70) return TrendingDown;
    if (val > 40) return TrendingUp;
    return CheckCircle2;
  };
  const getCongestionStatusColor = (val: number | undefined) => {
    if (val === undefined || val === null) return "text-muted-foreground";
    if (val > 70) return "text-destructive";
    if (val > 40) return "text-warning";
    return "text-green-500";
  };

  const getSpeedStatusIcon = (val: number | undefined) => {
    if (val === undefined || val === null) return undefined;
    if (val < 20) return TrendingDown;
    if (val < 40) return TrendingUp;
    return CheckCircle2;
  };
  const getSpeedStatusColor = (val: number | undefined) => {
    if (val === undefined || val === null) return "text-muted-foreground";
    if (val < 20) return "text-destructive";
    if (val < 40) return "text-warning";
    return "text-green-500";
  };

  const getIncidentStatusIcon = (val: number | undefined) => {
    if (val === undefined || val === null) return undefined;
    if (val > 0) return AlertTriangle;
    return ShieldCheck;
  };
  const getIncidentStatusColor = (val: number | undefined) => {
    if (val === undefined || val === null) return "text-muted-foreground";
    if (val > 0) return "text-destructive";
    return "text-green-500";
  };

  const getHealthStatus = (val: number | undefined) => {
    if (val === undefined || val === null) return "SYNCHRONIZING";
    if (val > 90) return "OPTIMAL";
    if (val > 70) return "STABLE";
    if (val > 40) return "DEGRADED";
    return "CRITICAL";
  };

  const getHealthColor = (val: number | undefined) => {
    if (val === undefined || val === null) return "text-primary";
    if (val > 90) return "text-green-700";
    if (val > 70) return "text-primary";
    if (val > 40) return "text-warning";
    return "text-destructive";
  };

  function isLatLng(obj: unknown): obj is { latitude: number; longitude: number } {
    return (
      typeof obj === 'object' &&
      obj !== null &&
      typeof (obj as { latitude?: unknown }).latitude === 'number' &&
      typeof (obj as { longitude?: unknown }).longitude === 'number'
    );
  }

  const congestionIndex = kpis?.congestion_index ?? null;
  const activeIncidents = kpis?.active_incidents_count ?? null;
  const averageSpeed = kpis?.average_speed_kmh ?? null;
  const totalFlow = kpis?.total_flow ?? null;

  const runningFeeds = feeds.filter(f => f.status === 'running').length;

  return (
    <AuthGuard requiredRole={UserRole.VIEWER}>
      <DashboardShell>
          <div className="retro-title-container">
              <div className="flex flex-col md:flex-row justify-between items-end gap-4">
                  <div>
                      <div className="flex items-center gap-3 mb-2">
                          <div className="flex gap-1">
                              <div className="h-2 w-2 bg-red-500 rounded-full animate-pulse" />
                              <div className="h-2 w-2 bg-yellow-500 rounded-full animate-pulse" style={{ animationDelay: '0.2s' }} />
                              <div className="h-2 w-2 bg-green-500 rounded-full animate-pulse" style={{ animationDelay: '0.4s' }} />
                          </div>
                          <span className="text-[10px] font-bold uppercase tracking-[0.3em] text-lcd-text/60">System Live // v4.2.0-stable</span>
                      </div>
                      <h1 className="text-5xl font-black uppercase tracking-tighter font-lcd matrix-glow text-lcd-text mb-1">Command Dashboard</h1>
                      <div className="flex items-center gap-4">
                          <div className="flex items-center gap-2">
                              <Terminal size={12} className="text-lcd-text/60" />
                              <span className="terminal-text text-[10px]">OS.TRAFFIC_HUB.SYS.01 // SESSION_ACTIVE</span>
                              <span className="text-[10px] opacity-40">// 128-BIT ENCRYPTION</span>
                          </div>
                          <div className="flex gap-1 h-6">
                              <button 
                                onClick={() => setActiveTab('overview')}
                                className={cn(
                                  "px-3 text-[10px] font-bold uppercase tracking-widest transition-all border-2",
                                  activeTab === 'overview' ? "bg-lcd-text text-lcd-bg border-lcd-text" : "border-lcd-text/20 text-lcd-text/60 hover:border-lcd-text/40"
                                )}
                              >
                                OVERVIEW
                              </button>
                              <button 
                                onClick={() => setActiveTab('incidents')}
                                className={cn(
                                  "px-3 text-[10px] font-bold uppercase tracking-widest transition-all border-2",
                                  activeTab === 'incidents' ? "bg-lcd-text text-lcd-bg border-lcd-text" : "border-lcd-text/20 text-lcd-text/60 hover:border-lcd-text/40"
                                )}
                              >
                                INCIDENT_TERMINAL {alerts.length > 0 && `(${alerts.length})`}
                              </button>
                          </div>
                      </div>
                  </div>
                  <div className="flex items-center gap-8 bg-lcd-text/5 p-4 border-2 border-lcd-text shadow-inner relative overflow-hidden group">
                      <div className="absolute inset-0 bg-gradient-to-r from-transparent via-lcd-text/5 to-transparent -translate-x-full group-hover:translate-x-full transition-transform duration-1000" />
                      <div className="flex flex-col items-end">
                          <div className="flex items-center gap-2 opacity-60 mb-1">
                              <Cpu size={10} />
                              <span className="text-[9px] uppercase font-bold">Node Integrity</span>
                          </div>
                          <span className="text-3xl font-bold font-lcd tabular-nums text-primary relative">
                              {runningFeeds}/{feeds.length}
                              <span className="absolute -right-4 top-0 h-full w-1 bg-primary/30 animate-pulse" />
                          </span>
                      </div>
                      <div className="w-px h-12 bg-lcd-text/20"></div>
                      <div className="flex flex-col items-end">
                          <div className="flex items-center gap-2 opacity-60 mb-1">
                              <Globe size={10} />
                              <span className="text-[9px] uppercase font-bold">Engine Status</span>
                          </div>
                          <div className="flex items-center gap-2">
                              <div className={cn(
                                  "h-2 w-2 rounded-full animate-pulse",
                                  (kpis?.global_health_score ?? 100) > 40 ? "bg-green-600" : "bg-red-600"
                              )} />
                              <span className={cn("text-3xl font-bold font-lcd", getHealthColor(kpis?.global_health_score as number))}>
                                  {getHealthStatus(kpis?.global_health_score as number)}
                                  {typeof kpis?.global_health_score === 'number' && (
                                      <span className="text-xs ml-1 opacity-50">[{kpis.global_health_score.toFixed(0)}%]</span>
                                  )}
                              </span>
                          </div>
                      </div>
                  </div>
              </div>
          </div>

          {activeTab === 'overview' ? (
            <div className="animate-in fade-in duration-500">
              {/* Real-time Analytics Cards */}
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-8 mb-12">
                <StatCard
                  title="Congestion Index"
                  value={`${typeof congestionIndex === 'number' ? congestionIndex : '--'}%`}
                  icon={Activity}
                  statusIcon={getCongestionStatusIcon(typeof congestionIndex === 'number' ? congestionIndex : undefined)}
                  statusColor={getCongestionStatusColor(typeof congestionIndex === 'number' ? congestionIndex : undefined)}
                  change={congestionTrend.change}
                  changeText={congestionTrend.text}
                />
                <StatCard
                  title="Average Speed"
                  value={`${typeof averageSpeed === 'number' ? averageSpeed.toFixed(1) : '--'} km/h`}
                  icon={Zap}
                  statusIcon={getSpeedStatusIcon(typeof averageSpeed === 'number' ? averageSpeed : undefined)}
                  statusColor={getSpeedStatusColor(typeof averageSpeed === 'number' ? averageSpeed : undefined)}
                  change={speedTrend.change}
                  changeText={speedTrend.text}
                />
                <StatCard
                  title="Active Incidents"
                  value={`${typeof activeIncidents === 'number' ? activeIncidents : '--'}`}
                  icon={AlertTriangle}
                  statusIcon={getIncidentStatusIcon(typeof activeIncidents === 'number' ? activeIncidents : undefined)}
                  statusColor={getIncidentStatusColor(typeof activeIncidents === 'number' ? activeIncidents : undefined)}
                  change="0"
                  changeText="No recent changes"
                />
                <StatCard
                  title="Total Flow"
                  value={`${typeof totalFlow === 'number' ? totalFlow : '--'}`}
                  icon={Users}
                  change={flowTrend.change}
                  changeText={flowTrend.text}
                />
              </div>

              <div className="grid grid-cols-1 xl:grid-cols-3 gap-8 mb-12">
                {/* Trend Chart and Lane Analysis */}
                <div className="xl:col-span-2 flex flex-col gap-8">
                  {/* Trend Chart */}
                  <div className="matrix-card flex flex-col min-h-[500px] relative group">
                    <div className="matrix-card-header">
                      <div className="flex items-center gap-2">
                          <BarChart2 size={16} />
                          <span>Real-time Flow Analysis // Telemetry Stream</span>
                      </div>
                      <Link href="/dashboard/analytics" className="text-[10px] flex items-center gap-1 hover:underline">
                          Expand Dataset <ChevronRight size={10} />
                      </Link>
                    </div>
                    <div className="matrix-card-content flex-1 flex flex-col relative overflow-hidden">
                      <div className="absolute inset-0 pointer-events-none bg-[linear-gradient(rgba(18,16,16,0)_50%,rgba(0,0,0,0.1)_50%),linear-gradient(90deg,rgba(255,0,0,0.03),rgba(0,255,0,0.01),rgba(0,0,255,0.03))] z-10 bg-[length:100%_2px,3px_100%]" />
                      <div className="flex-1 w-full min-h-0 pt-4 z-0">
                        <FlowAnalysisChart data={kpiHistory} timeRange="day" isLoading={!isReady && kpiHistory.length === 0} />
                      </div>
                    </div>
                  </div>

                  {/* New Lane Analysis Section */}
                  {feeds.length > 0 && (
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                      <LaneAnalysisWidget
                        title={`LANE METRICS: ${formatFeedName(feeds[0].name, feeds[0].feed_id)}`}
                        occupancy={feeds[0].latest_metrics?.lane_occupancy || {}}
                        queues={feeds[0].latest_metrics?.queue_lengths || {}}
                      />
                      {feeds.length > 1 ? (
                        <LaneAnalysisWidget
                          title={`LANE METRICS: ${formatFeedName(feeds[1].name, feeds[1].feed_id)}`}
                          occupancy={feeds[1].latest_metrics?.lane_occupancy || {}}
                          queues={feeds[1].latest_metrics?.queue_lengths || {}}
                        />
                      ) : (
                        <div className="matrix-card p-6 flex items-center justify-center text-lcd-text/40 tracking-widest text-sm">
                          ADDITIONAL FEED DATA UNAVAILABLE
                        </div>
                      )}
                    </div>
                  )}
                </div>

                {/* Side Panel: Eco Stats, Alerts */}
                <div className="flex flex-col gap-8">
                    {/* Eco Stats - collapsible */}
                    {showLazyWidgets && (
                      <EcoStatsWidget
                          vehicles={feeds.length > 0 ? (feeds[0].latest_vehicles || []) : []}
                      />
                  )}

                    {/* Alerts / Anomalies */}
                    <div className="matrix-card flex flex-col min-h-[400px]">
                        <div className="matrix-card-header">
                            <div className="flex items-center gap-2">
                                <ShieldCheck size={16} />
                                <span>Security Intelligence // Active Alerts</span>
                            </div>
                            <Badge className="bg-destructive text-white border-none rounded-none text-[8px] h-4">
                                {alerts.length} NEW
                            </Badge>
                        </div>
                        
                        <div className="matrix-card-content flex-1 overflow-y-auto custom-scrollbar">
                            {!isReady && (
                                <div className="h-full flex flex-col items-center justify-center text-lcd-text/50 gap-4">
                                    <Loader2 className="animate-spin h-8 w-8" />
                                    <span className="text-[10px] font-bold uppercase tracking-widest">Synchronizing...</span>
                                </div>
                            )}
                            
                            {isReady && alerts.length === 0 && (
                                <div className="h-full flex flex-col items-center justify-center text-lcd-text/50 gap-4 opacity-30">
                                    <ShieldCheck size={48} strokeWidth={1} />
                                    <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-center">System Nominal<br/>No Breaches Detected</span>
                                </div>
                            )}
                            
                            <div className="space-y-4">
                                {alerts.slice().reverse().map((alert) => (
                                <AnomalyItem
                                    key={alert.id || new Date(alert.timestamp).toISOString() + Math.random()}
                                    timestamp={new Date(alert.timestamp).toLocaleTimeString()}
                                    severity={alert.severity || 'info'}
                                    message={alert.message}
                                    onSelect={() => handleAnomalySelect(alert)}
                                    location={
                                    alert.details && typeof alert.details === 'object' && isLatLng(alert.details.location)
                                        ? `${alert.details.location.latitude.toFixed(3)}N, ${alert.details.location.longitude.toFixed(3)}E`
                                        : 'REGIONAL'
                                    }
                                    details={typeof alert.details === 'object' ? alert.details : undefined}
                                />
                                ))}
                            </div>
                        </div>
                        
                        <div className="p-4 border-t-2 border-lcd-text bg-lcd-text/5">
                            <Link href="/anomalies" className="flex items-center justify-center gap-2 text-[10px] font-bold uppercase hover:underline">
                                Audit All System Anomalies <ChevronRight size={12} />
                            </Link>
                        </div>
                    </div>
                </div>
              </div>

              {/* Video Feeds Section */}
              <section className="space-y-6">
                <div className="flex justify-between items-end border-b-4 border-lcd-text pb-2">
                    <div>
                        <h2 className="text-3xl font-black uppercase tracking-tighter font-lcd text-lcd-text">Surveillance Grid</h2>
                        <p className="text-[10px] font-bold uppercase opacity-60 mt-1">Direct Neural Uplink // Processing Pool: Edge-Alpha</p>
                    </div>
                    <div className="flex items-center gap-4">
                        <Link href="/surveillance">
                            <Button className="matrix-btn-sleek h-10">
                                Matrix Configuration
                            </Button>
                        </Link>
                    </div>
                </div>
                
                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-8">
                  {isReady && feeds.length > 0 ? (
                    feeds.slice(0, 8).map(feed => (
                      <div 
                        key={feed.feed_id} 
                        ref={el => { if (el) feedRefs.current.set(feed.feed_id, el); }}
                        className="matrix-card p-0 group overflow-hidden"
                      >
                        <div className="bg-lcd-text text-lcd-bg px-2 py-0.5 text-[8px] font-bold uppercase flex justify-between">
                            <span>NODE_{feed.feed_id.slice(-4)}</span>
                            <span>{feed.status.toUpperCase()}</span>
                        </div>
                        <SurveillanceFeed feed_id={feed.feed_id} minimalControls={true} />
                      </div>
                    ))
                  ) : (
                    <div className="col-span-full py-24 matrix-card flex flex-col items-center justify-center text-lcd-text border-dashed border-4">
                      {!isConnected ? (
                          <>
                            <Loader2 className="animate-spin mb-6 h-12 w-12 opacity-50" />
                            <p className="tracking-[0.5em] font-lcd text-2xl font-black opacity-50">ESTABLISHING UPLINK...</p>
                          </>
                      ) : (
                          <>
                            <Activity className="h-16 w-16 mb-4 opacity-20" />
                            <p className="tracking-[0.3em] font-lcd text-2xl font-black mb-2 uppercase">Zero Active Uplinks</p>
                            <p className="text-xs font-bold uppercase opacity-60 mb-8 tracking-widest">Infrastructure Ready // Awaiting Stream Input</p>
                            <Link href="/surveillance">
                                <Button className="matrix-btn-sleek px-12 h-12">
                                    Initialize Feeds
                                </Button>
                            </Link>
                          </>
                      )}
                    </div>
                  )}
                </div>
              </section>

                
                {feeds.length > 8 && (
                    <div className="mt-8 text-center">
                        <Link href="/surveillance" className="inline-flex items-center gap-2 px-8 py-3 bg-transparent text-lcd-text border border-lcd-text hover:bg-lcd-text hover:text-lcd-bg transition-all font-bold tracking-[0.2em] text-sm uppercase">
                            View All {feeds.length} Feeds <ChevronRight size={18} />
                        </Link>
                    </div>
                )}
            </div>
          ) : (
            <div className="mb-12 animate-in fade-in duration-500">
              <IncidentCommandCenter 
                alerts={alerts} 
                onJumpToFeed={handleJumpToFeed}
                onIncidentUpdated={(id, status) => {
                  // Alerts hook handles state update, but we can trigger local sync if needed
                }}
              />
            </div>
          )}
          
          <AnomalyDetailsModal 
            anomaly={selectedAnomaly} 
            open={isAnomalyModalOpen} 
            onOpenChange={setIsAnomalyModalOpen} 
          />
      </DashboardShell>
    </AuthGuard>
  );
};

export default DashboardPage;
