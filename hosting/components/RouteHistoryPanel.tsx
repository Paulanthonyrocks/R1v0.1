"use client";
import React, { useEffect, useState } from 'react';
import { ArrowRight, History, Search, XOctagon } from 'lucide-react';
import { APIClient } from '@/lib/api/APIClient';
import { getApiBrowserBaseURL } from '@/lib/api/backendBaseUrl';
import { roadsService } from '@/lib/services/roadsService';
import { parseFeedQuery } from '@/lib/utils/feedSearch';
import { routeMatchesQuery } from '@/lib/utils/recordingSearch';
import { Road } from '@/lib/types/api';

interface RouteAnalytics {
  most_common_routes: { start: string; end: string; count: number }[];
  time_of_day_histogram: number[];
  average_distance_km: number;
  average_duration_min: number;
  total_routes_analyzed: number;
}

type RouteHistoryResponse = RouteAnalytics | { message: string };

const RouteHistoryPanel: React.FC = () => {
  const [historyData, setHistoryData] = useState<RouteHistoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState('');
  const [roads, setRoads] = useState<Road[]>([]);

  useEffect(() => {
    roadsService.listRoads().then(setRoads).catch(() => setRoads([]));
  }, []);

  // Road-language filter over logged traversals: road:"Main St" keeps routes
  // touching one of that road's intersection nodes; free text matches node ids.
  const visibleRoutes = !historyData || 'message' in historyData
    ? []
    : historyData.most_common_routes.filter(r => routeMatchesQuery(r, parseFeedQuery(query), roads));


  useEffect(() => {
    let active = true;
    const client = APIClient.getInstance({ baseURL: getApiBrowserBaseURL() });
    client.get<RouteHistoryResponse>('/api/v1/routes/history/analytics', { limit: '20' })
      .then(data => { if (active) setHistoryData(data); })
      .catch(() => { if (active) setError('Failed to load route history archives'); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  if (loading) return <div className="text-center py-20 uppercase font-bold animate-pulse tracking-widest opacity-50">Retrieving Historical Log...</div>;
  if (error) return (
    <div className="p-10 text-red-500 border-2 border-red-500 flex flex-col items-center gap-4">
      <XOctagon className="h-12 w-12" />
      <p className="font-bold uppercase tracking-widest">{error}</p>
    </div>
  );
  if (!historyData || 'message' in historyData || !historyData.total_routes_analyzed) return (
    <div className="py-20 text-center border-2 border-dashed border-lcd-text/20 rounded opacity-50">
      <p className="tracking-widest uppercase font-bold">No historical data found</p>
    </div>
  );

  return (
    <div className="max-w-6xl mx-auto w-full space-y-8">
      <div>
        <h1 className="text-4xl font-bold uppercase tracking-tighter flex items-center gap-3">
          <History size={32} className="text-primary" /> Logged Traversals
        </h1>
        <p className="text-lcd-text/60 mt-2">Analysis of the latest {historyData.total_routes_analyzed} recorded routes (up to 20).</p>
        <div className="relative mt-4 max-w-md font-bold group">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 opacity-40 group-focus-within:opacity-100 transition-opacity" size={14} />
          <input
            type="text"
            placeholder='FILTER road:"Main St" or node "34.0000,-117.9950"...'
            className="bg-lcd-text/5 border-2 border-lcd-text/30 text-lcd-text rounded-none p-3 pl-9 w-full focus:outline-none focus:border-lcd-text tracking-[0.15em] placeholder:text-lcd-text/20 font-black uppercase text-[10px]"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </div>
      <div className="grid grid-cols-3 gap-4">
        <HistoryStat label="Routes Analyzed" value={String(historyData.total_routes_analyzed)} />
        <HistoryStat label="Avg Duration" value={`${historyData.average_duration_min}m`} />
        <HistoryStat label="Avg Distance" value={`${historyData.average_distance_km}km`} />
      </div>
      <div className="matrix-card overflow-hidden">
        <h2 className="p-4 font-bold uppercase">Most Common Routes{query.trim() && <span className="opacity-50">{' // ' + visibleRoutes.length + ' match "' + query + '"'}</span>}</h2>
        <table className="w-full text-sm">
          <thead><tr><th className="p-4 text-left">Trajectory</th><th className="p-4 text-left">Traversals</th></tr></thead>
          <tbody>
            {visibleRoutes.map((route, index) => (
              <tr key={`${route.start}-${route.end}-${index}`} className="border-t border-lcd-text/10">
                <td className="p-4"><div className="flex items-center gap-2"><span>{route.start}</span><ArrowRight size={14} /><span>{route.end}</span></div></td>
                <td className="p-4">{route.count}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

const HistoryStat = ({ label, value }: { label: string; value: string }) => (
  <div className="matrix-card p-4 flex flex-col justify-center">
    <span className="text-[10px] uppercase opacity-60 font-bold mb-1 tracking-widest">{label}</span>
    <span className="text-2xl font-bold tracking-tighter">{value}</span>
  </div>
);

export default RouteHistoryPanel;
