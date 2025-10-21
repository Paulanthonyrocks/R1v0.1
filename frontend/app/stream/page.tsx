"use client";

import React, { useEffect, useState } from 'react';
import { Chart } from "react-google-charts";
import MatrixCard from '../../components/MatrixCard';
import { Signal, Clock, BatteryFull, Activity, Zap, AlertTriangle, Users } from 'lucide-react'; // Import missing icons
import StatCard from '@/components/dashboard/StatCard'; // Import StatCard
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';

const MAX_HISTORY = 20;

const StreamPage: React.FC = () => {
  const { kpis: metrics, feeds } = useRealtimeUpdates();
  const [history, setHistory] = useState<{ timestamp: string, congestion: number, speed: number }[]>([]);

  useEffect(() => {
    if (metrics) {
      setHistory(prev => {
        const now = new Date().toLocaleTimeString();
        const newEntry = {
          timestamp: now,
          congestion: typeof metrics.vehicle_count === 'number' ? metrics.vehicle_count : 0, // Using vehicle_count as placeholder for congestion
          speed: typeof metrics.avg_speed === 'number' ? metrics.avg_speed : 0, // Using avg_speed for average_speed_kmh
        };
        const updated = [...prev, newEntry];
        return updated.length > MAX_HISTORY ? updated.slice(-MAX_HISTORY) : updated;
      });
    }
  }, [metrics]);

  // Removed getCongestionColor, getSpeedColor, getIncidentColor as colors are neutralized

  // Chart data
  const chartData = [
    ["Time", "Congestion Index", "Average Speed"],
    ...history.map(h => [h.timestamp, h.congestion, h.speed])
  ];
  const chartOptions = {
    title: "Real-Time Trends",
    fontName: 'monospace', // Apply monospace broadly
    curveType: "function",
    backgroundColor: 'transparent', // Assuming chart area is on a green background from MatrixCard
    titleTextStyle: { color: 'hsl(var(--primary))', fontName: 'monospace' }, // Black
    hAxis: {
      textStyle: { color: 'hsl(var(--primary))', fontName: 'monospace' },
      titleTextStyle: { color: 'hsl(var(--primary))', fontName: 'monospace' } // For hAxis title, if any
    },
    vAxis: {
      textStyle: { color: 'hsl(var(--primary))', fontName: 'monospace' },
      titleTextStyle: { color: 'hsl(var(--primary))', fontName: 'monospace' } // For vAxis title, if any
    },
    legend: { position: "bottom", textStyle: { color: 'hsl(var(--primary))', fontName: 'monospace' } }, // Black legend
    tooltip: { textStyle: { color: 'hsl(var(--primary))', fontName: 'monospace' } }, // Tooltip text
    series: {
      0: { color: 'hsl(var(--primary))', lineDashStyle: [4, 4] }, // Dashed line for Congestion
      1: { color: 'hsl(var(--primary))' }                     // Solid line for Speed
    },
  };

  return (
      <div className="bg-lcd-bg text-lcd-text font-lcd flex flex-col min-h-screen w-full">
        {/* Status Bar */}
        <header className="flex items-center justify-between px-4 py-1 border-b-2 border-lcd-text">
          <div className="flex items-center space-x-2">
            <Signal size={20} />
            <span className="font-lcd matrix-glow">STREAM</span>
          </div>
          <div className="flex items-center space-x-2">
            <Clock size={20} />
            <span className="font-lcd matrix-glow">{new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
            <BatteryFull size={20} />
          </div>
        </header>
        <div className="p-4 grid gap-4 grid-cols-1 md:grid-cols-2 lg:grid-cols-3 relative">
          <h1 className="text-2xl font-bold col-span-full mb-4 text-lcd-text tracking-normal">Real-Time Stream</h1> {/* Added text-lcd-text tracking-normal */}

          {/* Metric Cards */}
          <div className="col-span-full grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
            <StatCard title="Vehicles Detected" value={String(metrics?.vehicle_count ?? '--')} unit="" icon={Activity} change="N/A" changeText="N/A" />
            <StatCard title="Average Speed" value={metrics?.avg_speed ? metrics.avg_speed.toFixed(2) : '--'} unit="km/h" icon={Zap} change="N/A" changeText="N/A" />
            <StatCard title="Active Incidents" value={String(metrics?.active_incidents_count ?? '--')} icon={AlertTriangle} change="N/A" changeText="N/A" />
            <StatCard title="Feeds" value={String(feeds ? feeds.filter(f => f.status === 'running').length : '--')} unit="/ running" icon={Users} change="N/A" changeText="N/A">
              <div className="text-xs text-lcd-text mt-1 tracking-normal"> {/* Changed color, added tracking */}
                {feeds && (
                  <>
                    <span className="mr-2">Stopped: <span>{feeds.filter(f => f.status === 'stopped').length}</span></span> {/* Removed specific color */}
                    <span>Error: <span>{feeds.filter(f => f.status === 'error').length}</span></span> {/* Removed specific color */}
                  </>
                )}
              </div>
            </StatCard>
          </div>

          {/* Chart */}
          <div className="col-span-full">
            <MatrixCard title="Trends" className="pixel-drop-shadow"> {/* Added pixel-drop-shadow */}
              <Chart
                chartType="LineChart"
                width="100%"
                height="400px"
                data={chartData}
                options={chartOptions}
              />
            </MatrixCard>
          </div>
        </div>
      </div>
  );
};

export default StreamPage;