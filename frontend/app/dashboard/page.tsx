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
        
        const updated = [...prev, newPoint];
        if (updated.length > maxHistoryPoints) {
          return updated.slice(updated.length - maxHistoryPoints);
        }
        return updated;
      });
    }
  }, [kpis]);

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
    if (val > 70) return "text-red-500";
    if (val > 40) return "text-yellow-500";
    return "text-green-500";
  };

  const getSpeedStatusIcon = (val: number | undefined) => {
    if (val === undefined || val === null) return undefined;
    if (val < 20) return TrendingDown;
    if (val < 40) return TrendingUp;
    return CheckCircle2;
  };
  const getSpeedStatusColor = (val: number | undefined) => {
    if (val === undefined || val === null) return undefined;
    if (val < 20) return "text-red-500";
    if (val < 40) return "text-yellow-500";
    return "text-green-500";
  };

  const getIncidentStatusIcon = (val: number | undefined) => {
    if (val === undefined || val === null) return undefined;
    if (val > 0) return AlertTriangle;
    return ShieldCheck;
  };
  const getIncidentStatusColor = (val: number | undefined) => {
    if (val === undefined || val === null) return undefined;
    if (val > 0) return "text-red-500";
    return "text-green-500";
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
          <div className="flex flex-col md:flex-row justify-between items-center mb-6 gap-4">
              <h1 className="text-3xl font-bold uppercase tracking-[0.2em] text-center md:text-left font-lcd matrix-glow text-lcd-bg">SYSTEM OVERVIEW</h1>
              <div className="flex items-center gap-4 bg-lcd-bg text-lcd-text p-2 px-4 rounded-sm border border-lcd-text/20">
                  <div className="flex flex-col items-center">
                      <span className="text-[10px] opacity-60">ACTIVE FEEDS</span>
                      <span className="text-xl font-bold">{runningFeeds}/{feeds.length}</span>
                  </div>
                  <div className="w-px h-8 bg-lcd-text/20"></div>
                  <div className="flex flex-col items-center">
                      <span className="text-[10px] opacity-60">UPTIME</span>
                      <span className="text-xl font-bold">99.9%</span>
                  </div>
              </div>
          </div>

          {/* Real-time Analytics Cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
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

          <div className="grid grid-cols-1 xl:grid-cols-3 gap-6 mb-8">
            {/* Trend Chart */}
            <div className="xl:col-span-2 matrix-card p-4 h-[400px]">
              <div className="flex justify-between items-center mb-4">
                <h2 className="text-xl font-semibold tracking-normal font-lcd matrix-glow text-lcd-text">REAL-TIME FLOW ANALYSIS</h2>
                <Link href="/dashboard/analytics" className="text-[10px] flex items-center gap-1 hover:underline">
                    VIEW DETAILS <ChevronRight size={12} />
                </Link>
              </div>
              <div className="h-[320px] w-full">
                <FlowAnalysisChart data={kpiHistory} timeRange="day" isLoading={!isReady && kpiHistory.length === 0} />
              </div>
            </div>

            {/* Side Panel: Quick Status & Alerts */}
            <div className="flex flex-col gap-6">
                <div className="matrix-card p-4 flex-1">
                    <div className="flex justify-between items-center mb-3">
                        <h2 className="text-xl font-semibold tracking-normal font-lcd matrix-glow text-lcd-text">SYSTEM ALERTS</h2>
                        <span className="text-[10px] bg-red-900 text-white px-2 py-0.5 rounded-full">{alerts.length} NEW</span>
                    </div>
                    {!isReady && <p className="text-lcd-text text-sm animate-pulse">CONNECTING...</p>}
                    {isReady && alerts.length === 0 && <p className="text-lcd-text text-xs opacity-60">SYSTEM STATUS NOMINAL. NO ALERTS.</p>}
                    <div className="space-y-3 max-h-[280px] overflow-y-auto pr-2 custom-scrollbar">
                        {alerts.slice(-5).reverse().map((alert) => (
                        <AnomalyItem
                            key={alert.id || new Date(alert.timestamp).toISOString() + Math.random()}
                            timestamp={new Date(alert.timestamp).toLocaleTimeString()}
                            severity={alert.severity || 'info'}
                            message={alert.message}
                            onSelect={handleAnomalySelect}
                            location={
                            alert.details && typeof alert.details === 'object' && isLatLng(alert.details.location)
                                ? `Lat: ${alert.details.location.latitude.toFixed(3)}, Lon: ${alert.details.location.longitude.toFixed(3)}`
                                : 'N/A'
                            }
                            details={typeof alert.details === 'object' ? alert.details : undefined}
                        />
                        ))}
                    </div>
                    {alerts.length > 5 && (
                        <Link href="/anomalies" className="block text-center text-[10px] mt-4 hover:underline text-lcd-text/70">
                            VIEW ALL ALERTS
                        </Link>
                    )}
                </div>
            </div>
          </div>

          {/* Video Feeds Section */}
          <div className="mb-8">
            <div className="flex justify-between items-center mb-4 px-1">
                <h2 className="text-2xl font-bold tracking-widest font-lcd matrix-glow text-lcd-bg">ACTIVE SURVEILLANCE</h2>
                <div className="flex items-center gap-4">
                    <span className="text-xs text-lcd-bg/70 uppercase">Available Feeds: {feeds.length}</span>
                    <Link href="/surveillance">
                        <Button variant="outline" size="sm" className="bg-lcd-bg text-lcd-text border-lcd-text hover:bg-lcd-text hover:text-lcd-bg text-[10px] h-7">
                            MANAGE ALL
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
                <div className="col-span-full py-12 matrix-card flex flex-col items-center justify-center opacity-50">
                   {!isConnected ? (
                       <>
                        <Loader2 className="animate-spin mb-4" />
                        <p className="tracking-[0.2em] font-lcd">ESTABLISHING UPLINK...</p>
                       </>
                   ) : (
                       <p className="tracking-[0.2em] font-lcd">NO ACTIVE FEEDS DETECTED</p>
                   )}
                </div>
              )}
            </div>
            
            {feeds.length > 8 && (
                <div className="mt-6 text-center">
                    <Link href="/surveillance" className="inline-flex items-center gap-2 px-6 py-2 bg-lcd-bg text-lcd-text border-2 border-lcd-text hover:bg-lcd-text hover:text-lcd-bg transition-all font-bold tracking-widest text-sm">
                        VIEW ALL {feeds.length} FEEDS <ChevronRight size={18} />
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
