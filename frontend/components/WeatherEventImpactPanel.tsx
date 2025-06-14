import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { AlertTriangle, ShieldAlert, Info } from 'lucide-react';

interface WeatherData {
  temperature: number;
  conditions: string;
  precipitation_chance: number;
  wind_speed: number;
}

interface EventData {
  type: string;
  description: string;
  severity: string;
  location: string;
  start_time: string;
  end_time?: string;
}

interface WeatherEventImpact {
  type: 'weather' | 'event';
  description: string;
  severity: string;
  location: string;
  startTime: string;
  endTime?: string;
  details?: WeatherData | EventData;
}

const WeatherEventImpactPanel: React.FC = () => {
  const [impacts, setImpacts] = useState<WeatherEventImpact[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchData = async () => {
      try {
        // Get current location - in production, use proper geolocation
        const defaultLat = 34.0522;
        const defaultLon = -118.2437;

        const [weatherResponse, eventsResponse] = await Promise.all([
          axios.get(`/api/v1/weather/current?lat=${defaultLat}&lon=${defaultLon}`),
          axios.get('/api/v1/events/current')
        ]);

        const weatherData = weatherResponse.data;
        const eventsData = eventsResponse.data;

        // Transform weather data into impact format
        const weatherImpact: WeatherEventImpact = {
          type: 'weather',
          description: `${weatherData.conditions} - ${weatherData.temperature}°C`,
          severity: getSeverityFromWeather(weatherData),
          location: 'Current Location',
          startTime: new Date().toISOString(),
          details: weatherData
        };

        // Transform events into impact format
        const eventImpacts: WeatherEventImpact[] = eventsData.map((event: EventData) => ({
          type: 'event',
          description: event.description,
          severity: event.severity,
          location: event.location,
          startTime: event.start_time,
          endTime: event.end_time,
          details: event
        }));

        setImpacts([weatherImpact, ...eventImpacts]);
      } catch (err) {
        setError('Failed to load weather and event impacts');
        console.error('Error fetching impacts:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  const getSeverityFromWeather = (weather: WeatherData): string => {
    if (weather.precipitation_chance > 70 || weather.wind_speed > 50) {
      return 'High';
    } else if (weather.precipitation_chance > 30 || weather.wind_speed > 30) {
      return 'Medium';
    }
    return 'Low';
  };

  if (loading) return <div className="p-4 text-primary tracking-normal">Loading weather and event impacts...</div>;
  if (error) return <div className="p-4 text-primary tracking-normal"><AlertTriangle className="inline-block h-5 w-5 mr-2 text-primary" />{error}</div>;
  if (!impacts.length) return <div className="p-4 text-primary tracking-normal">No weather or event impacts found.</div>;

  return (
    <div className="p-4 bg-background border border-primary pixel-drop-shadow">
      <h2 className="text-xl font-bold mb-4 tracking-normal">Weather & Event Impacts</h2>
      <div className="space-y-4">
        {impacts.map((impact, idx) => (
          <div key={idx} className="p-4 border border-primary bg-background">
            <div className="flex items-center justify-between mb-2">
              <span className="font-bold uppercase tracking-normal">{impact.type}</span>
              <span className="px-2 py-1 text-sm bg-primary text-primary-foreground tracking-normal font-bold uppercase">
                {impact.severity === 'High' && <AlertTriangle className="inline-block h-4 w-4 mr-1 text-primary-foreground" />}
                {impact.severity === 'Medium' && <ShieldAlert className="inline-block h-4 w-4 mr-1 text-primary-foreground" />}
                {impact.severity === 'Low' && <Info className="inline-block h-4 w-4 mr-1 text-primary-foreground" />}
                {impact.severity} Impact
              </span>
            </div>
            <p className="text-foreground tracking-normal">{impact.description}</p>
            <div className="mt-2 text-sm text-primary tracking-normal space-y-1">
              <p>Location: {impact.location}</p>
              <p>Start: {new Date(impact.startTime).toLocaleString()}</p>
              {impact.endTime && (
                <p>End: {new Date(impact.endTime).toLocaleString()}</p>
              )}
              {impact.type === 'weather' && impact.details && (
                <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-primary tracking-normal">
                  <p>Wind: {(impact.details as WeatherData).wind_speed} km/h</p>
                  <p>Precipitation: {(impact.details as WeatherData).precipitation_chance}%</p>
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default WeatherEventImpactPanel;
