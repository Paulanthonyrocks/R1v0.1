"use client";

import React, { useEffect, useState, useMemo } from 'react';
import Link from 'next/link';
import AuthGuard from "@/components/auth/AuthGuard";
import { Activity, Zap, AlertTriangle, Users, TrendingDown, TrendingUp, CheckCircle2, ShieldCheck, ChevronRight, Loader2 } from 'lucide-react';
import { UserRole } from "@/lib/auth/roles";
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import { Button } from '@/components/ui/button';

import AnomalyItem from '@/components/dashboard/AnomalyItem';
import StatCard from '@/components/dashboard/StatCard';
import SurveillanceFeed from '@/components/dashboard/SurveillanceFeed';
import FlowAnalysisChart from '@/components/dashboard/FlowAnalysisChart';
import AnomalyDetailsModal from '@/components/dashboard/AnomalyDetailsModal';
import DashboardShell from '@/components/dashboard/DashboardShell';
import { TrendDataPoint, AlertData } from '@/lib/types';
import LaneAnalysisWidget from '@/components/dashboard/LaneAnalysisWidget';
import LaneAnalysisWidget from '@/components/dashboard/LaneAnalysisWidget';
import AnomalyList from '@/components/dashboard/AnomalyList';
import { analyticsService } from '@/lib/services/analyticsService';
import EcoStatsWidget from '@/components/dashboard/EcoStatsWidget';
import AIInsightsPanel from '@/components/dashboard/AIInsightsPanel';

const DashboardPage: React.FC = () => {
  const { kpis, alerts, feeds, isConnected, isReady } = useRealtimeUpdates();
  const [kpiHistory, setKpiHistory] = useState<TrendDataPoint[]>([]);
  const [selectedAnomaly, setSelectedAnomaly] = useState<AlertData | null>(null);
  const [isAnomalyModalOpen, setIsAnomalyModalOpen] = useState(false);
  const maxHistoryPoints = 60; // Keep 60 points (e.g., 1 minute of data if updated every second)

  const handleAnomalySelect = (alert: AlertData) => {
    setSelectedAnomaly(alert);
    setIsAnomalyModalOpen(true);
  };

  // Update KPI history when new KPIs arrive
  useEffect(() => {
    if (kpis) {
      setKpiHistory(prev => {
        const newPoint: TrendDataPoint = {
          timestamp: new Date().toISOString(),
          total_vehicles: kpis.total_flow ?? 0,
          avg_speed: kpis.average_speed_kmh ?? 0,
          congestion_index: kpis.congestion_index ?? 0
        };

        // Append new point
        const updated = [...prev, newPoint];
        // If we have a lot of history (e.g. from DB), we might want to keep it?
        // But for real-time visualization, we usually limit the window.
        // If we want to show 24h trend, we shouldn't slice to 60.
        // Let's increase limit if we have history loaded, or handle separately.
        // For now, let's just keep last 1000 points to accommodate mixed data
        const limit = prev.length > 60 ? 1000 : 60;

        if (updated.length > limit) {
          return updated.slice(updated.length - limit);
        }
        return updated;
      });
    }
  }, [kpis]);

  // Load historical data
  useEffect(() => {
    const loadHistory = async () => {
      // Find a suitable feed to show history for (running > first)
      const targetFeed = feeds.find(f => f.status === 'running') || feeds[0];
      if (!targetFeed) return;

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
            // Only set if empty to avoid overwriting real-time updates
            if (prev.length === 0) return mappedHistory;
            return prev;
          });
        }
      } catch (err) {
        console.error("Failed to load feed history:", err);
      }
    };

    if (isReady && feeds.length > 0 && kpiHistory.length === 0) {
      loadHistory();
    }
  }, [isReady, feeds]);

  // Calculate trends for StatCards
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

  // Helper functions to determine status icons and colors
  const getCongestionStatusIcon = (val: number | undefined) => {
    if (val === undefined || val === null) return undefined;
    if (val > 70) return TrendingDown;
    if (val > 40) return TrendingUp;
    return CheckCircle2;
  };
  const getCongestionStatusColor = (val: number | undefined) => {
    if (val === undefined || val === null) return undefined;
    if (val > 70) return "text-destructive";
    if (val > 40) return "text-warning";
    return undefined;
  };

  const getSpeedStatusIcon = (val: number | undefined) => {
    if (val === undefined || val === null) return undefined;
    if (val < 20) return TrendingDown;
    if (val < 40) return TrendingUp;
    return CheckCircle2;
  };
  const getSpeedStatusColor = (val: number | undefined) => {
    if (val === undefined || val === null) return undefined;
    if (val < 20) return "text-destructive";
    if (val < 40) return "text-warning";
    return undefined;
  };

  const getIncidentStatusIcon = (val: number | undefined) => {
    if (val === undefined || val === null) return undefined;
    if (val > 0) return AlertTriangle;
    return ShieldCheck;
  };
  const getIncidentStatusColor = (val: number | undefined) => {
    if (val === undefined || val === null) return undefined;
    if (val > 0) return "text-destructive";
    return undefined;
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

  const congestionIndex = kpis?.congestion_index ?? null;
  const activeIncidents = kpis?.active_incidents_count ?? null;
  const averageSpeed = kpis?.average_speed_kmh ?? null;
  const totalFlow = kpis?.total_flow ?? null;

  const runningFeeds = feeds.filter(f => f.status === 'running').length;

  return (
    <AuthGuard requiredRole={UserRole.VIEWER}>
      <DashboardShell>
        <div className="flex flex-col md:flex-row justify-between items-end mb-8 gap-4 border-b-2 border-lcd-text/20 pb-4">
          <div>
            <h1 className="text-4xl font-bold uppercase tracking-[0.2em] font-lcd matrix-glow text-lcd-text/90 mb-2">SYSTEM OVERVIEW</h1>
            <p className="text-sm text-lcd-text/60 font-lcd tracking-widest">REAL-TIME TRAFFIC MONITORING HUB</p>
          </div>
          <div className="flex items-center gap-6">
            <div className="flex flex-col items-end">
              <span className="text-[10px] uppercase tracking-wider opacity-60">Active Feeds</span>
              <span className="text-2xl font-bold font-lcd matrix-glow">{runningFeeds}/{feeds.length}</span>
            </div>
            <div className="w-px h-10 bg-lcd-text/20"></div>
            <div className="flex flex-col items-end">
              <span className="text-[10px] uppercase tracking-wider opacity-60">System Status</span>
              <span className="text-2xl font-bold font-lcd matrix-glow text-primary">ONLINE</span>
            </div>
          </div>
        </div>

        {/* Real-time Analytics Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6 mb-8">
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


        {/* Main Grid: Flow, Lane Analysis, Alerts */}
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 mb-8">
          {/* Trend Chart */}
          <div className="xl:col-span-2 flex flex-col gap-6">
            <div className="matrix-card p-6 h-[400px] flex flex-col">
              <div className="flex justify-between items-center mb-6">
                <h2 className="text-xl font-bold tracking-widest font-lcd matrix-glow text-lcd-text">REAL-TIME FLOW ANALYSIS</h2>
                <Link href="/dashboard/analytics" className="text-xs flex items-center gap-2 hover:text-lcd-text/80 transition-colors uppercase tracking-wider">
                  Full Report <ChevronRight size={14} />
                </Link>
              </div>
              <div className="flex-1 w-full min-h-0">
                <FlowAnalysisChart data={kpiHistory} timeRange="day" isLoading={!isReady && kpiHistory.length === 0} />
              </div>
            </div>

            {/* New Lane Analysis Section */}
            {feeds.length > 0 && (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <LaneAnalysisWidget
                  title={`LANE METRICS: ${feeds[0].feed_id.split('-')[0].toUpperCase()}`} // Just showing 1st feed for demo
                  occupancy={feeds[0].latest_metrics?.lane_occupancy || {}}
                  queues={feeds[0].latest_metrics?.queue_lengths || {}}
                />
                {/* Placeholder for second feed or other widget */}
                {feeds.length > 1 ? (
                  <LaneAnalysisWidget
                    title={`LANE METRICS: ${feeds[1].feed_id.split('-')[0].toUpperCase()}`}
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

          {/* Side Panel: Anomalies & Alerts & AI Insights */}
          <div className="flex flex-col h-full gap-6">
            <AIInsightsPanel
              metrics={kpis}
              feedName={feeds.length > 0 ? feeds[0].name : "System"}
            />

            <EcoStatsWidget
              vehicles={feeds.length > 0 ? (feeds[0].latest_vehicles || []) : []}
            />

            <div className="matrix-card p-4 flex-1 flex flex-col min-h-[400px]">
              <AnomalyList
                anomalies={alerts}
                onSelect={handleAnomalySelect}
              />

              {alerts.length > 0 && (
                <div className="mt-4 pt-4 border-t border-lcd-text/10">
                  <Link href="/anomalies" className="flex items-center justify-center gap-2 text-xs hover:underline text-lcd-text/70 uppercase tracking-widest">
                    View All Alerts <ChevronRight size={12} />
                  </Link>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Video Feeds Section */}
        <div className="mb-8">
          <div className="flex justify-between items-end mb-6 border-b border-lcd-text/10 pb-2">
            <div>
              <h2 className="text-2xl font-bold tracking-[0.2em] font-lcd matrix-glow text-lcd-text">ACTIVE SURVEILLANCE</h2>
              <p className="text-xs text-lcd-text/50 tracking-widest mt-1">LIVE VIDEO FEEDS</p>
            </div>
            <div className="flex items-center gap-4">
              <Link href="/surveillance">
                <Button variant="outline" size="sm" className="bg-transparent border-lcd-text text-lcd-text hover:bg-lcd-text hover:text-lcd-bg text-xs h-8 uppercase tracking-widest rounded-none">
                  Manage Feeds
                </Button>
              </Link>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4 gap-6">
            {isReady && feeds.length > 0 ? (
              feeds.slice(0, 8).map(feed => (
                <div key={feed.feed_id} className="min-w-0">
                  <SurveillanceFeed feed={feed} />
                </div>
              ))
            ) : (
              <div className="col-span-full py-20 matrix-card flex flex-col items-center justify-center text-lcd-text/40 border-dashed">
                {!isConnected ? (
                  <>
                    <Loader2 className="animate-spin mb-4 h-8 w-8" />
                    <p className="tracking-[0.2em] font-lcd text-lg">ESTABLISHING UPLINK...</p>
                  </>
                ) : (
                  <>
                    <div className="mb-4 relative">
                      <div className="absolute inset-0 bg-lcd-text/10 blur-xl rounded-full"></div>
                      <Activity className="h-12 w-12 relative z-10" />
                    </div>
                    <p className="tracking-[0.2em] font-lcd text-xl font-bold mb-2">NO ACTIVE FEEDS</p>
                    <p className="text-sm opacity-60 max-w-md text-center mb-6">No surveillance feeds are currently active. Start a feed to begin monitoring.</p>
                    <Link href="/surveillance">
                      <Button className="bg-lcd-text text-lcd-bg hover:bg-lcd-text/90 rounded-none uppercase tracking-widest font-bold">
                        Configure Feeds
                      </Button>
                    </Link>
                  </>
                )}
              </div>
            )}
          </div>

          {feeds.length > 8 && (
            <div className="mt-8 text-center">
              <Link href="/surveillance" className="inline-flex items-center gap-2 px-8 py-3 bg-transparent text-lcd-text border border-lcd-text hover:bg-lcd-text hover:text-lcd-bg transition-all font-bold tracking-[0.2em] text-sm uppercase">
                View All {feeds.length} Feeds <ChevronRight size={18} />
              </Link>
            </div>
          )}
        </div>

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
