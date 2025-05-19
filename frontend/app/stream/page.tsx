"use client";

import React, { useEffect, useState } from 'react';
import useSWR from 'swr';
import { Chart } from "react-google-charts";
import MatrixCard from '../../components/MatrixCard';
import MatrixButton from '../../components/MatrixButton';
import AuthGuard from "@/components/auth/AuthGuard";
import { UserRole } from "@/lib/auth/roles";

const fetcher = (url: string) => fetch(url).then(res => res.json());

const MetricCard = ({ title, value, unit, color, children }: { title: string, value: React.ReactNode, unit?: string, color?: string, children?: React.ReactNode }) => (
  <div className={`bg-gray-800 p-4 rounded shadow text-center flex flex-col items-center justify-center min-w-[140px]`}>
    <div className="text-xs text-gray-400 uppercase mb-1">{title}</div>
    <div className={`text-2xl font-bold mb-1 ${color ? color : ''}`}>{value}{unit && <span className="text-base font-normal ml-1">{unit}</span>}</div>
    {children}
  </div>
);

const MAX_HISTORY = 20;

const StreamPage: React.FC = () => {
  const { data: metrics } = useSWR('/v1/analytics/realtime', fetcher, { refreshInterval: 5000 });
  const [history, setHistory] = useState<{ timestamp: string, congestion: number, speed: number }[]>([]);

  useEffect(() => {
    if (metrics) {
      setHistory(prev => {
        const now = new Date().toLocaleTimeString();
        const newEntry = {
          timestamp: now,
          congestion: typeof metrics.congestion_index === 'number' ? metrics.congestion_index : 0,
          speed: typeof metrics.average_speed_kmh === 'number' ? metrics.average_speed_kmh : 0,
        };
        const updated = [...prev, newEntry];
        return updated.length > MAX_HISTORY ? updated.slice(-MAX_HISTORY) : updated;
      });
    }
  }, [metrics]);

  const getCongestionColor = (val: number) => val > 70 ? 'text-red-400' : val > 40 ? 'text-yellow-400' : 'text-green-400';
  const getSpeedColor = (val: number) => val < 20 ? 'text-red-400' : val < 40 ? 'text-yellow-400' : 'text-green-400';
  const getIncidentColor = (val: number) => val > 0 ? 'text-red-400' : 'text-green-400';

  // Chart data
  const chartData = [
    ["Time", "Congestion Index", "Average Speed"],
    ...history.map(h => [h.timestamp, h.congestion, h.speed])
  ];
  const chartOptions = {
    title: "Real-Time Trends",
    curveType: "function",
    legend: { position: "bottom" },
    backgroundColor: 'transparent',
    titleTextStyle: { color: 'white' },
    hAxis: { textStyle: { color: 'white' } },
    vAxis: { textStyle: { color: 'white' } },
    series: {
      0: { color: '#f87171' }, // Congestion
      1: { color: '#34d399' }, // Speed
    },
  };

  return (
    <AuthGuard requiredRole={UserRole.OPERATOR}>
      <div className="p-4 grid gap-4 grid-cols-1 md:grid-cols-2 lg:grid-cols-3 relative">
        <h1 className="text-2xl font-bold col-span-full mb-4">Real-Time Stream</h1>

        {/* Metric Cards */}
        <div className="col-span-full grid grid-cols-2 md:grid-cols-4 gap-4 mb-4">
          <MetricCard title="Congestion Index" value={metrics?.congestion_index ?? '--'} unit="%" color={metrics ? getCongestionColor(metrics.congestion_index) : ''} />
          <MetricCard title="Average Speed" value={metrics?.average_speed_kmh ?? '--'} unit="km/h" color={metrics ? getSpeedColor(metrics.average_speed_kmh) : ''} />
          <MetricCard title="Active Incidents" value={metrics?.active_incidents_count ?? '--'} color={metrics ? getIncidentColor(metrics.active_incidents_count) : ''} />
          <MetricCard title="Feeds" value={metrics?.feed_statuses ? metrics.feed_statuses.running : '--'} unit="/ running">
            <div className="text-xs text-gray-400 mt-1">
              {metrics?.feed_statuses && (
                <>
                  <span className="mr-2">Stopped: <span className="text-yellow-400">{metrics.feed_statuses.stopped}</span></span>
                  <span>Error: <span className="text-red-400">{metrics.feed_statuses.error}</span></span>
                </>
              )}
            </div>
          </MetricCard>
        </div>

        {/* Chart */}
        <div className="col-span-full">
          <MatrixCard title="Trends">
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
    </AuthGuard>
  );
};

export default StreamPage;