"use client";
import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { AlertTriangle, XOctagon, CheckCircle2, History, ArrowRight } from 'lucide-react';
import { cn } from '@/lib/utils';

interface RouteAnalytics {
  total_routes: number;
  avg_duration: number;
  avg_traffic_impact: string;
  common_weather_impacts: string[];
}

interface RouteHistoryEntry {
  id: string;
  origin: string;
  destination: string;
  routeSummary: string;
  date: string;
  duration: number;
  distance: number;
  trafficImpact: string;
  weatherImpact?: string;
}

interface RouteHistoryData {
  routes: RouteHistoryEntry[];
  analytics: RouteAnalytics;
}

const RouteHistoryPanel: React.FC = () => {
  const [historyData, setHistoryData] = useState<RouteHistoryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [timeRange, setTimeRange] = useState('week');

  useEffect(() => {
    const fetchData = async () => {
      try {
        const end = new Date();
        const start = new Date();
        
        if (timeRange === 'day') {
          start.setDate(start.getDate() - 1);
        } else if (timeRange === 'week') {
          start.setDate(start.getDate() - 7);
        } else if (timeRange === 'month') {
          start.setMonth(start.getMonth() - 1);
        }

        const response = await axios.get('/api/v1/route-history/analytics', {
          params: {
            start_date: start.toISOString(),
            end_date: end.toISOString()
          }
        });

        setHistoryData(response.data);
      } catch (err) {
        setError('Failed to load route history archives');
        console.error('Error fetching route history:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, [timeRange]);

  if (loading) return <div className="text-center py-20 uppercase font-bold animate-pulse tracking-widest opacity-50">Retrieving Historical Log...</div>;
  if (error) return (
    <div className="p-10 text-red-500 border-2 border-red-500 flex flex-col items-center gap-4">
      <XOctagon className="h-12 w-12" />
      <p className="font-bold uppercase tracking-widest">{error}</p>
    </div>
  );
  
  if (!historyData?.routes.length) return (
      <div className="py-20 text-center border-2 border-dashed border-lcd-text/20 rounded opacity-50">
          <p className="tracking-widest uppercase font-bold">No historical data found for this period</p>
      </div>
  );

  return (
    <div className="max-w-6xl mx-auto w-full space-y-8">
      <div className="flex flex-col md:flex-row justify-between items-end mb-8 gap-4">
          <div>
              <h1 className="text-4xl font-bold uppercase tracking-tighter flex items-center gap-3">
                  <History size={32} className="text-primary" /> Logged Traversals
              </h1>
              <p className="text-lcd-text/60 mt-2">Historical analysis of corridor throughput and agent routing efficiency.</p>
          </div>
          <div className="flex bg-lcd-text/10 p-1 rounded border border-lcd-text/20">
              {(['day', 'week', 'month'] as const).map((r) => (
                  <button
                    key={r}
                    onClick={() => setTimeRange(r)}
                    className={`px-4 py-1 text-xs uppercase transition-all ${timeRange === r ? 'bg-lcd-text text-lcd-bg font-bold' : 'hover:bg-lcd-text/20'}`}
                  >
                      {r}
                  </button>
              ))}
          </div>
      </div>

      {/* Analytics Summary */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <HistoryStat label="Total Routes" value={historyData.analytics.total_routes.toString()} />
          <HistoryStat label="Avg Duration" value={`${Math.round(historyData.analytics.avg_duration / 60)}m`} />
          <HistoryStat label="Net Impact" value={historyData.analytics.avg_traffic_impact} capitalize />
          <HistoryStat label="Atmosphere" value={historyData.analytics.common_weather_impacts[0] || 'Clear'} />
      </div>

      {/* Routes Table */}
      <div className="matrix-card overflow-hidden">
        <div className="overflow-x-auto">
            <table className="w-full text-sm">
            <thead>
                <tr className="bg-lcd-text/10 text-primary border-b-2 border-lcd-text/20">
                <th className="p-4 text-left font-bold uppercase tracking-widest text-[10px]">Timestamp</th>
                <th className="p-4 text-left font-bold uppercase tracking-widest text-[10px]">Trajectory</th>
                <th className="p-4 text-left font-bold uppercase tracking-widest text-[10px]">Metrics</th>
                <th className="p-4 text-left font-bold uppercase tracking-widest text-[10px]">Conditions</th>
                </tr>
            </thead>
            <tbody>
                {historyData.routes.map(entry => (
                <tr key={entry.id} className="border-b border-lcd-text/5 hover:bg-lcd-text/5 transition-colors group">
                    <td className="p-4 whitespace-nowrap">
                        <div className="flex flex-col">
                            <span className="font-bold">{new Date(entry.date).toLocaleDateString()}</span>
                            <span className="text-[10px] opacity-60">{new Date(entry.date).toLocaleTimeString()}</span>
                        </div>
                    </td>
                    <td className="p-4">
                        <div className="flex items-center gap-2 font-medium">
                            <span className="truncate max-w-[150px]">{entry.origin}</span>
                            <ArrowRight size={14} className="opacity-40" />
                            <span className="truncate max-w-[150px]">{entry.destination}</span>
                        </div>
                        <div className="text-[10px] opacity-60 mt-1 uppercase tracking-tighter">{entry.routeSummary}</div>
                    </td>
                    <td className="p-4">
                        <div className="flex gap-4">
                            <div className="flex flex-col">
                                <span className="text-[10px] uppercase opacity-40">Time</span>
                                <span className="font-bold">{Math.round(entry.duration / 60)}m</span>
                            </div>
                            <div className="flex flex-col">
                                <span className="text-[10px] uppercase opacity-40">Dist</span>
                                <span className="font-bold">{(entry.distance / 1000).toFixed(1)}km</span>
                            </div>
                        </div>
                    </td>
                    <td className="p-4">
                        <div className="flex items-center gap-3">
                            <div className="flex items-center gap-1.5">
                                {(() => {
                                const impactText = entry.trafficImpact.toLowerCase();
                                if (impactText.includes('high')) return <XOctagon size={14} className="text-red-500" />;
                                if (impactText.includes('medium')) return <AlertTriangle size={14} className="text-yellow-500" />;
                                return <CheckCircle2 size={14} className="text-green-500" />;
                                })()}
                                <span className="text-xs uppercase font-bold">{entry.trafficImpact}</span>
                            </div>
                            {entry.weatherImpact && (
                                <div className="h-4 w-px bg-lcd-text/20"></div>
                            )}
                            <span className="text-xs opacity-60">{entry.weatherImpact}</span>
                        </div>
                    </td>
                </tr>
                ))}
            </tbody>
            </table>
        </div>
      </div>
    </div>
  );
};

const HistoryStat = ({ label, value, capitalize }: { label: string, value: string, capitalize?: boolean }) => (
    <div className="matrix-card p-4 flex flex-col justify-center">
        <span className="text-[10px] uppercase opacity-60 font-bold mb-1 tracking-widest">{label}</span>
        <span className={cn("text-2xl font-bold tracking-tighter", capitalize && "capitalize")}>{value}</span>
    </div>
);

export default RouteHistoryPanel;
