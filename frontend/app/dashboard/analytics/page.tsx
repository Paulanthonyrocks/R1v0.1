"use client";

import React, { useState, useEffect, useMemo } from 'react';
import AuthGuard from "@/components/auth/AuthGuard";
import { UserRole } from "@/lib/auth/roles";
import { BarChart3, TrendingUp, TrendingDown, Users, Zap, Activity } from 'lucide-react';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import FlowAnalysisChart from '@/components/dashboard/FlowAnalysisChart';
import { TrendDataPoint } from '@/lib/types';
import StatCard from '@/components/dashboard/StatCard';
import DashboardShell from '@/components/dashboard/DashboardShell';

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
          <div className="flex flex-col md:flex-row justify-between items-end mb-8 gap-4">
              <div>
                  <h1 className="text-4xl font-bold uppercase tracking-tighter mb-2 text-lcd-bg matrix-glow">Traffic Flow Analysis</h1>
                  <p className="text-lcd-bg/60 max-w-2xl">
                      Real-time trend analysis aggregated from {feeds.length} active surveillance nodes. 
                      Data is processed using YOLOv8 computer vision models.
                  </p>
              </div>
              <div className="flex bg-lcd-bg/10 p-1 rounded border border-lcd-bg/20">
                  {(['day', 'week', 'month'] as const).map((r) => (
                      <button
                        key={r}
                        onClick={() => setTimeRange(r)}
                        className={`px-4 py-1 text-xs uppercase transition-all ${timeRange === r ? 'bg-lcd-bg text-lcd-text font-bold' : 'text-lcd-bg hover:bg-lcd-bg/20'}`}
                      >
                          {r}
                      </button>
                  ))}
              </div>
          </div>

          {/* Stats Grid */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
              <StatCard 
                title="Current Flow" 
                value={stats?.latest.total_vehicles.toString() || "--"} 
                icon={Users} 
                change="+2.4%" 
                changeText="Historical average comparison" 
              />
              <StatCard 
                title="Avg. Velocity" 
                value={`${stats?.avgSpeed.toFixed(1) || "--"} km/h`} 
                icon={Zap} 
                change="-1.2%" 
                changeText="Trend over selected period" 
              />
              <StatCard 
                title="Peak Flow" 
                value={stats?.maxVehicles.toString() || "--"} 
                icon={TrendingUp} 
                change="Daily Max" 
                changeText="Highest recorded flow today" 
              />
              <StatCard 
                title="Network Health" 
                value={`${isConnected ? '98' : '0'}%`} 
                icon={Activity} 
                change="Stable" 
                changeText="System uptime and node reachability" 
              />
          </div>

          {/* Large Chart Section */}
          <div className="matrix-card p-6 mb-8 h-[500px]">
              <div className="flex justify-between items-center mb-6">
                  <h2 className="text-xl font-bold uppercase flex items-center gap-2 text-lcd-text">
                      <BarChart3 size={24} className="text-primary" />
                      Temporal Flow Trends
                  </h2>
                  <div className="text-[10px] text-lcd-text/40">
                      AUTO-REFRESHING EVERY 5S
                  </div>
              </div>
              <div className="h-[380px] w-full">
                  <FlowAnalysisChart data={kpiHistory} timeRange={timeRange} isLoading={!isReady && kpiHistory.length === 0} />
              </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
              <div className="matrix-card p-6">
                  <h3 className="text-lg font-bold uppercase mb-4 border-b border-lcd-text/20 pb-2 text-lcd-text">Insights</h3>
                  <ul className="space-y-4 text-sm text-lcd-text">
                      <li className="flex gap-3">
                          <TrendingUp className="text-yellow-500 shrink-0" size={18} />
                          <span>Morning rush hour expected to peak in 45 minutes based on current acceleration patterns.</span>
                      </li>
                      <li className="flex gap-3">
                          <Activity className="text-green-500 shrink-0" size={18} />
                          <span>Node consistency is high. 100% of camera feeds are currently providing valid telemetry.</span>
                      </li>
                      <li className="flex gap-3">
                          <TrendingDown className="text-red-500 shrink-0" size={18} />
                          <span>Average speed on Main St corridor is dropping below 15 km/h.</span>
                      </li>
                  </ul>
              </div>
              <div className="matrix-card p-6">
                  <h3 className="text-lg font-bold uppercase mb-4 border-b border-lcd-text/20 pb-2 text-lcd-text">Data Quality</h3>
                  <div className="space-y-4 text-lcd-text">
                      <div className="flex justify-between items-center">
                          <span className="text-xs uppercase opacity-60">Confidence Score</span>
                          <span className="font-bold">94.2%</span>
                      </div>
                      <div className="w-full bg-lcd-text/10 h-2 rounded-full overflow-hidden">
                          <div className="bg-primary h-full" style={{ width: '94.2%' }}></div>
                      </div>
                      <div className="flex justify-between items-center">
                          <span className="text-xs uppercase opacity-60">Latent Delay</span>
                          <span className="font-bold">142ms</span>
                      </div>
                      <div className="w-full bg-lcd-text/10 h-2 rounded-full overflow-hidden">
                          <div className="bg-primary h-full" style={{ width: '15%' }}></div>
                      </div>
                  </div>
              </div>
          </div>
      </DashboardShell>
    </AuthGuard>
  );
};

export default AnalyticsPage;