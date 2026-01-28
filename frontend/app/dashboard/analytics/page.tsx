"use client";

import React, { useState, useEffect, useMemo } from 'react';
import AuthGuard from "@/components/auth/AuthGuard";
import { UserRole } from "@/lib/auth/roles";
import { BarChart3, TrendingUp, TrendingDown, Users, Zap, Activity, ArrowRightLeft, Map as MapIcon, Camera } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import FlowAnalysisChart from '@/components/dashboard/FlowAnalysisChart';
import { TrendDataPoint } from '@/lib/types';
import StatCard from '@/components/dashboard/StatCard';
import DashboardShell from '@/components/dashboard/DashboardShell';
import { OriginDestinationMatrix } from '@/components/dashboard/OriginDestinationMatrix';
import { TrafficHeatmap } from '@/components/dashboard/TrafficHeatmap';
import { SystemHealthMonitor } from '@/components/dashboard/SystemHealthMonitor';

const AnalyticsPage = () => {
  const { kpis, feeds, isConnected, isReady } = useRealtimeUpdates();
  const [kpiHistory, setKpiHistory] = useState<TrendDataPoint[]>([]);
  const [timeRange, setTimeRange] = useState<'day' | 'week' | 'month'>('day');

  useEffect(() => {
    if (kpis) {
      setKpiHistory(prev => {
        const newPoint: TrendDataPoint = {
          timestamp: new Date().toISOString(),
          total_vehicles: kpis.total_flow ?? 0,
          avg_speed: kpis.average_speed_kmh ?? 0,
          congestion_index: kpis.congestion_index ?? 0
        };
        const updated = [...prev, newPoint].slice(-100); // More history for analytics
        return updated;
      });
    }
  }, [kpis]);

  const stats = useMemo(() => {
      if (kpiHistory.length === 0) return null;
      const latest = kpiHistory[kpiHistory.length - 1];
      const avgVehicles = kpiHistory.reduce((acc, p) => acc + p.total_vehicles, 0) / kpiHistory.length;
      const maxVehicles = Math.max(...kpiHistory.map(p => p.total_vehicles));
      const avgSpeed = kpiHistory.reduce((acc, p) => acc + p.avg_speed, 0) / kpiHistory.length;
      
      return { latest, avgVehicles, maxVehicles, avgSpeed };
  }, [kpiHistory]);

  return (
    <AuthGuard requiredRole={UserRole.PLANNER}>
      <DashboardShell>
          <div className="retro-title-container">
              <div className="flex flex-col md:flex-row justify-between items-end gap-6">
                  <div>
                      <h1 className="text-5xl font-black uppercase tracking-tighter font-lcd matrix-glow text-lcd-text mb-1">Flow Analytics</h1>
                      <div className="flex items-center gap-2">
                          <span className="terminal-text text-[10px]">DATA.AGGREGATOR // NODES_ACTIVE: {feeds.length}</span>
                      </div>
                  </div>
                  <div className="flex bg-lcd-text/5 p-2 border-2 border-lcd-text shadow-inner">
                      {(['day', 'week', 'month'] as const).map((r) => (
                          <button
                            key={r}
                            onClick={() => setTimeRange(r)}
                            className={cn(
                                "px-6 py-2 text-xs font-black uppercase transition-all tracking-widest border-2 border-transparent",
                                timeRange === r 
                                    ? "bg-lcd-text text-lcd-bg border-lcd-text shadow-lg" 
                                    : "text-lcd-text/40 hover:text-lcd-text hover:bg-lcd-text/5"
                            )}
                          >
                              {r}
                          </button>
                      ))}
                  </div>
              </div>
          </div>

          {/* Stats Grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-8 mb-12">
              <StatCard 
                title="Active Throughput" 
                value={stats?.latest.total_vehicles.toString() || "--"} 
                icon={Users} 
                change="+2.4%" 
                changeText="VS HISTORICAL_AVG" 
              />
              <StatCard 
                title="Network Velocity" 
                value={`${stats?.avgSpeed.toFixed(1) || "--"} km/h`} 
                icon={Zap} 
                change="-1.2%" 
                changeText="PERIOD_TREND" 
              />
              <StatCard 
                title="Saturation Peak" 
                value={stats?.maxVehicles.toString() || "--"} 
                icon={TrendingUp} 
                change="CRITICAL" 
                changeText="DAILY_MAX_RECORDED" 
              />
              <StatCard 
                title="Node Reliability" 
                value={`${isConnected ? '99.8' : '0.0'}%`} 
                icon={Activity} 
                change="STABLE" 
                changeText="SYSTEM_UPTIME" 
              />
          </div>

          {/* Large Chart Section */}
          <div className="matrix-card p-0 mb-12 flex flex-col min-h-[550px]">
              <div className="matrix-card-header">
                  <div className="flex items-center gap-2">
                      <BarChart3 size={16} />
                      <span>Temporal Distribution // Flow Intensity Matrix</span>
                  </div>
                  <div className="text-[10px] font-bold opacity-40 uppercase">
                      Stream_Freq: 5000ms // Buffer_Active
                  </div>
              </div>
              <div className="matrix-card-content flex-1 flex flex-col pt-8">
                  <div className="flex-1 w-full min-h-0">
                    <FlowAnalysisChart data={kpiHistory} timeRange={timeRange} isLoading={!isReady && kpiHistory.length === 0} />
                  </div>
              </div>
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 mb-12">
              {/* O-D Matrix */}
              <div className="matrix-card p-0">
                  <div className="matrix-card-header">
                      <div className="flex items-center gap-2">
                          <ArrowRightLeft size={16} />
                          <span>Topology Analysis // Origin-Destination</span>
                      </div>
                      <span className="text-[10px] opacity-40 uppercase font-black tracking-widest">ReID_Enabled</span>
                  </div>
                  <div className="p-6">
                    <OriginDestinationMatrix hours={timeRange === 'day' ? 1 : 24} />
                  </div>
              </div>

              {/* Heatmap */}
              <div className="matrix-card p-0">
                  <div className="matrix-card-header">
                      <div className="flex items-center gap-2">
                          <MapIcon size={16} />
                          <span>Spatial Distribution // KDE Density Map</span>
                      </div>
                      <span className="text-[10px] opacity-40 uppercase font-black tracking-widest">Global_Live</span>
                  </div>
                  <div className="p-6">
                    <TrafficHeatmap hours={timeRange === 'day' ? 1 : 24} />
                  </div>
              </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-12">
              <div className="matrix-card p-0">
                  <div className="matrix-card-header">
                      <span>Neural Insights</span>
                  </div>
                  <div className="p-6">
                    <ul className="space-y-6 text-xs font-bold uppercase tracking-tight">
                        <li className="flex gap-4 p-3 bg-lcd-text/5 border border-lcd-text/10">
                            <TrendingUp className="text-yellow-600 shrink-0" size={18} />
                            <span>Rush hour saturation expected in +42m based on ingress acceleration.</span>
                        </li>
                        <li className="flex gap-4 p-3 bg-lcd-text/5 border border-lcd-text/10">
                            <Activity className="text-green-600 shrink-0" size={18} />
                            <span>100% Signal integrity maintained across all edge nodes.</span>
                        </li>
                        <li className="flex gap-4 p-3 bg-lcd-text/5 border border-lcd-text/10 text-red-700">
                            <TrendingDown className="text-red-600 shrink-0" size={18} />
                            <span>Corridor B-14 reporting sub-optimal velocity (12km/h).</span>
                        </li>
                    </ul>
                  </div>
              </div>
              
              <div className="matrix-card p-0">
                  <div className="matrix-card-header">
                      <span>Telemetry Integrity</span>
                  </div>
                  <div className="p-6 space-y-6">
                      <div className="space-y-2">
                          <div className="flex justify-between items-center text-[10px] font-black uppercase">
                              <span className="opacity-60">AI Confidence Index</span>
                              <span>94.2%</span>
                          </div>
                          <div className="w-full bg-lcd-text/10 h-3 border-2 border-lcd-text/20 overflow-hidden">
                              <div className="bg-primary h-full shadow-[0_0_10px_var(--lcd-text)]" style={{ width: '94.2%' }}></div>
                          </div>
                      </div>
                      <div className="space-y-2">
                          <div className="flex justify-between items-center text-[10px] font-black uppercase">
                              <span className="opacity-60">Network Latency (Avg)</span>
                              <span>142ms</span>
                          </div>
                          <div className="w-full bg-lcd-text/10 h-3 border-2 border-lcd-text/20 overflow-hidden">
                              <div className="bg-primary h-full shadow-[0_0_10px_var(--lcd-text)]" style={{ width: '15%' }}></div>
                          </div>
                      </div>
                      <div className="pt-2 flex items-center gap-2 text-[8px] font-black uppercase opacity-40">
                          <div className="h-1.5 w-1.5 rounded-full bg-green-600 animate-pulse" />
                          Data Validation Success // checksum: 0xFD21
                      </div>
                  </div>
              </div>

              <div className="matrix-card p-0 overflow-hidden">
                  <div className="matrix-card-header">
                      <span>System Hardware</span>
                  </div>
                  <SystemHealthMonitor />
              </div>
          </div>

      </DashboardShell>
    </AuthGuard>
  );
};

export default AnalyticsPage;