import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { AlertTriangle, XOctagon, CheckCircle2 } from 'lucide-react'; // Import error icon & traffic impact icons

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

  if (loading) return <div className="p-4">Loading route history...</div>; // Loading text already themed in previous step
  if (error) return ( // Added icon to error message
    <div className="p-4 text-primary tracking-normal flex items-center">
      <AlertTriangle className="h-5 w-5 mr-2 flex-shrink-0" /> {/* Icon added, color inherited */}
      {error}
    </div>
  );
  if (!historyData?.routes.length) return <div className="p-4 text-primary tracking-normal">No route history found.</div>; {/* Added color & tracking */}

  return (
    <div className="p-4 bg-card rounded-lg border border-primary pixel-drop-shadow"> {/* Removed shadow-lg, added border and pixel-drop-shadow */}
      <div className="flex justify-between items-center mb-6">
        <h2 className="text-xl font-bold text-primary tracking-normal">Route History</h2> {/* Added text-primary tracking-normal */}
        <div className="flex gap-2">
          <select
            value={timeRange}
            onChange={(e) => setTimeRange(e.target.value)}
            className="px-3 py-1 border border-primary rounded bg-matrix-panel text-primary tracking-normal focus:ring-2 focus:ring-primary focus:outline-none focus:border-primary" // Added focus styling
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
        <div className="p-3 bg-card rounded-lg border border-primary pixel-drop-shadow"> {/* Added border and shadow */}
          <div className="text-sm text-muted-foreground tracking-normal">Total Routes</div> {/* Changed color, added tracking */}
          <div className="text-xl font-semibold text-primary tracking-normal">{historyData.analytics.total_routes}</div> {/* Added color & tracking */}
        </div>
        <div className="p-3 bg-card rounded-lg border border-primary pixel-drop-shadow"> {/* Added border and shadow */}
          <div className="text-sm text-muted-foreground tracking-normal">Avg Duration</div> {/* Changed color, added tracking */}
          <div className="text-xl font-semibold text-primary tracking-normal">{Math.round(historyData.analytics.avg_duration / 60)} min</div> {/* Added color & tracking */}
        </div>
        <div className="p-3 bg-card rounded-lg border border-primary pixel-drop-shadow"> {/* Added border and shadow */}
          <div className="text-sm text-muted-foreground tracking-normal">Traffic Impact</div> {/* Changed color, added tracking */}
          <div className="text-xl font-semibold text-primary tracking-normal capitalize">{historyData.analytics.avg_traffic_impact}</div> {/* Added color & tracking */}
        </div>
        <div className="p-3 bg-card rounded-lg border border-primary pixel-drop-shadow"> {/* Added border and shadow */}
          <div className="text-sm text-muted-foreground tracking-normal">Common Weather</div> {/* Changed color, added tracking */}
          <div className="text-sm font-semibold text-primary tracking-normal">{historyData.analytics.common_weather_impacts.join(', ')}</div> {/* Added color & tracking */}
        </div>
      </div>

      {/* Routes Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm border border-primary"> {/* Added border-primary for table border */}
          <thead>
            <tr className="bg-primary text-primary-foreground"> {/* Themed header row */}
              <th className="p-2 border border-primary tracking-normal">Date</th> {/* Added tracking, themed border */}
              <th className="p-2 border border-primary tracking-normal">Route</th> {/* Added tracking, themed border */}
              <th className="p-2 border border-primary tracking-normal">Duration</th> {/* Added tracking, themed border */}
              <th className="p-2 border border-primary tracking-normal">Distance</th> {/* Added tracking, themed border */}
              <th className="p-2 border border-primary tracking-normal">Traffic</th> {/* Added tracking, themed border */}
              <th className="p-2 border border-primary tracking-normal">Weather</th> {/* Added tracking, themed border */}
            </tr>
          </thead>
          <tbody className="tracking-normal"> {/* Added tracking-normal to be inherited by td */}
            {historyData.routes.map(entry => (
              <tr key={entry.id} className="border-b border-primary hover:bg-black/10"> {/* Themed border and hover */}
                <td className="p-2 border border-primary whitespace-nowrap">{new Date(entry.date).toLocaleString()}</td> {/* Themed border */}
                <td className="p-2 border border-primary"> {/* Themed border */}
                  <div className="font-medium text-primary">{entry.origin} → {entry.destination}</div> {/* Ensured text-primary */}
                  <div className="text-xs text-muted-foreground tracking-normal">{entry.routeSummary}</div> {/* Changed color, added tracking */}
                </td>
                <td className="p-2 border border-primary whitespace-nowrap">{Math.round(entry.duration / 60)} min</td> {/* Themed border */}
                <td className="p-2 border border-primary whitespace-nowrap">{(entry.distance / 1000).toFixed(1)} km</td> {/* Themed border */}
                <td className="p-2 border border-primary">
                  <div className="flex items-center">
                    {(() => {
                      const impactText = entry.trafficImpact.toLowerCase();
                      let IconComponent = null;
                      if (impactText.includes('high')) IconComponent = XOctagon;
                      else if (impactText.includes('medium')) IconComponent = AlertTriangle;
                      else if (impactText.includes('low')) IconComponent = CheckCircle2;
                      return IconComponent ? <IconComponent className="h-4 w-4 mr-1.5 text-primary flex-shrink-0" /> : null;
                    })()}
                    <span className="capitalize text-primary tracking-normal">{entry.trafficImpact}</span>
                  </div>
                </td>
                <td className="p-2 border border-primary">{entry.weatherImpact || '-'}</td> {/* Themed border */}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default RouteHistoryPanel;
