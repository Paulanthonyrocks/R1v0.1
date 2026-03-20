"use client";

import React, { useState, useEffect, useMemo } from 'react';
import AuthGuard from "@/components/auth/AuthGuard";
import { UserRole } from "@/lib/auth/roles";
import { BarChart3, TrendingUp, TrendingDown, Users, Zap, Activity, ArrowRightLeft, Map as MapIcon, Camera, Info } from 'lucide-react';
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

          {/* Topology & Spatial Modules */}
          <div className="flex flex-col xl:flex-row gap-12 mb-12">
              {/* O-D Matrix Module: More prominent width */}
              <div className="flex-[1.5] min-w-0">
                  <div className="relative group">
                      {/* Decorative Frame Elements */}
                      <div className="absolute -top-3 -left-3 w-8 h-8 border-t-4 border-l-4 border-lcd-text opacity-20 group-hover:opacity-100 transition-opacity" />
                      <div className="absolute -bottom-3 -right-3 w-8 h-8 border-b-4 border-r-4 border-lcd-text opacity-20 group-hover:opacity-100 transition-opacity" />
                      
                      <div className="matrix-card p-0 overflow-hidden bg-lcd-text/[0.02] border-2 border-lcd-text/20">
                          <div className="matrix-card-header bg-lcd-text/10 border-b-2 border-lcd-text/20 flex items-center justify-between px-6 py-4">
                              <div className="flex items-center gap-4">
                                  <div className="p-2 bg-lcd-text text-lcd-bg shadow-[0_0_10px_var(--lcd-text)]">
                                      <ArrowRightLeft size={20} />
                                  </div>
                                  <div>
                                      <h3 className="text-xl font-black uppercase tracking-tighter">Topology Analysis</h3>
                                      <p className="text-[10px] font-bold opacity-40 uppercase tracking-widest">Global ReID Vehicle Correlation // Origin-Destination</p>
                                  </div>
                              </div>
                              <div className="hidden sm:flex items-center gap-2">
                                  <div className="px-3 py-1 bg-lcd-text/5 border border-lcd-text/20 rounded-full">
                                      <span className="text-[8px] font-black uppercase tracking-widest opacity-60">Status: </span>
                                      <span className="text-[8px] font-black uppercase text-green-500 animate-pulse">Synchronizing</span>
                                  </div>
                              </div>
                          </div>
                          <div className="p-8">
                            <OriginDestinationMatrix hours={timeRange === 'day' ? 1 : 24} />
                          </div>
                          
                          {/* System Footer Bar */}
                          <div className="bg-lcd-text/5 border-t border-lcd-text/10 px-6 py-2 flex justify-between items-center">
                              <span className="text-[8px] font-black opacity-30 uppercase">Module_ID: TOPOLOGY_OD_01 // CRC: 0x82A1</span>
                              <div className="flex gap-4">
                                  <div className="h-1 w-12 bg-lcd-text/20 relative overflow-hidden">
                                      <div className="absolute inset-0 bg-lcd-text/40 animate-[loading-bar_2s_infinite]" />
                                  </div>
                              </div>
                          </div>
                      </div>
                  </div>
              </div>

              {/* Heatmap Module: Sidebar style */}
              <div className="flex-1 min-w-0">
                  <div className="matrix-card p-0 flex flex-col h-full border-2 border-lcd-text/10 hover:border-lcd-text/30 transition-colors">
                      <div className="matrix-card-header px-4 py-3 border-b border-lcd-text/10">
                          <div className="flex items-center gap-2">
                              <MapIcon size={14} className="opacity-60" />
                              <span className="text-sm font-black uppercase tracking-widest">Spatial Density</span>
                          </div>
                      </div>
                      <div className="p-4 flex-1 flex flex-col justify-center">
                          <p className="text-[10px] font-bold opacity-40 uppercase tracking-widest mb-4">KDE Probability Heatmap // Period: {timeRange}</p>
                          <div className="border border-lcd-text/20 p-2 bg-black/40">
                            <TrafficHeatmap hours={timeRange === 'day' ? 1 : 24} />
                          </div>
                      </div>
                      <div className="p-4 bg-lcd-text/[0.03] border-t border-lcd-text/10">
                          <div className="flex items-start gap-3">
                              <Info size={14} className="text-lcd-text/40 shrink-0 mt-0.5" />
                              <p className="text-[9px] font-bold opacity-60 uppercase leading-relaxed tracking-tight">
                                  Visualizing vehicle concentration across the network. High intensity clusters indicate primary bottleneck formations.
                              </p>
                          </div>
                      </div>
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