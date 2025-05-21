import React, { useEffect, useState } from 'react';
import axios from 'axios';

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
  const [timeRange, setTimeRange] = useState('week'); // 'day', 'week', 'month'

  useEffect(() => {
    const fetchData = async () => {
      try {
        const end = new Date();
        const start = new Date();
        
        // Calculate start date based on time range
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
        setError('Failed to load route history');
        console.error('Error fetching route history:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, [timeRange]);

  if (loading) return <div className="p-4">Loading route history...</div>;
  if (error) return <div className="p-4 text-red-500">{error}</div>;
  if (!historyData?.routes.length) return <div className="p-4">No route history found.</div>;

  return (
    <div className="p-4 bg-white rounded-lg shadow-lg">
      <div className="flex justify-between items-center mb-6">
        <h2 className="text-xl font-bold">Route History</h2>
        <div className="flex gap-2">          <select
            value={timeRange}
            onChange={(e) => setTimeRange(e.target.value)}
            className="px-3 py-1 border rounded bg-white"
            aria-label="Time range filter"
            title="Select time range for route history"
          >
            <option value="day">Last 24 Hours</option>
            <option value="week">Last 7 Days</option>
            <option value="month">Last 30 Days</option>
          </select>
        </div>
      </div>

      {/* Analytics Summary */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div className="p-3 bg-gray-50 rounded-lg">
          <div className="text-sm text-gray-600">Total Routes</div>
          <div className="text-xl font-semibold">{historyData.analytics.total_routes}</div>
        </div>
        <div className="p-3 bg-gray-50 rounded-lg">
          <div className="text-sm text-gray-600">Avg Duration</div>
          <div className="text-xl font-semibold">{Math.round(historyData.analytics.avg_duration / 60)} min</div>
        </div>
        <div className="p-3 bg-gray-50 rounded-lg">
          <div className="text-sm text-gray-600">Traffic Impact</div>
          <div className="text-xl font-semibold capitalize">{historyData.analytics.avg_traffic_impact}</div>
        </div>
        <div className="p-3 bg-gray-50 rounded-lg">
          <div className="text-sm text-gray-600">Common Weather</div>
          <div className="text-sm font-semibold">{historyData.analytics.common_weather_impacts.join(', ')}</div>
        </div>
      </div>

      {/* Routes Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm border">
          <thead>
            <tr className="bg-gray-50">
              <th className="p-2 border">Date</th>
              <th className="p-2 border">Route</th>
              <th className="p-2 border">Duration</th>
              <th className="p-2 border">Distance</th>
              <th className="p-2 border">Traffic</th>
              <th className="p-2 border">Weather</th>
            </tr>
          </thead>
          <tbody>
            {historyData.routes.map(entry => (
              <tr key={entry.id} className="border-b hover:bg-gray-50">
                <td className="p-2 border whitespace-nowrap">{new Date(entry.date).toLocaleString()}</td>
                <td className="p-2 border">
                  <div className="font-medium">{entry.origin} → {entry.destination}</div>
                  <div className="text-xs text-gray-500">{entry.routeSummary}</div>
                </td>
                <td className="p-2 border whitespace-nowrap">{Math.round(entry.duration / 60)} min</td>
                <td className="p-2 border whitespace-nowrap">{(entry.distance / 1000).toFixed(1)} km</td>
                <td className="p-2 border">
                  <span className={`px-2 py-1 rounded text-xs ${
                    entry.trafficImpact.toLowerCase().includes('high') ? 'bg-red-100 text-red-800' :
                    entry.trafficImpact.toLowerCase().includes('medium') ? 'bg-yellow-100 text-yellow-800' :
                    'bg-green-100 text-green-800'
                  }`}>
                    {entry.trafficImpact}
                  </span>
                </td>
                <td className="p-2 border">{entry.weatherImpact || '-'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default RouteHistoryPanel;
