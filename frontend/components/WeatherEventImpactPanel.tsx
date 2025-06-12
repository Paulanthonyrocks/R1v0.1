import React, { useEffect, useState } from 'react';
import axios from 'axios';
import {
  AlertTriangle,
  CloudSun,
  Megaphone,
  ShieldAlert,
  ShieldHalf,
  ShieldCheck
} from 'lucide-react'; // Import icons

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

  if (loading) return <div className="p-4 text-primary tracking-normal">Loading weather and event impacts...</div>; {/* Themed */}
  if (error) return (
    <div className="p-4 text-primary tracking-normal flex items-center">
      <AlertTriangle className="h-5 w-5 mr-2 flex-shrink-0" /> {/* Icon added */}
      {error}
    </div>
  );
  if (!impacts.length) return <div className="p-4 text-primary tracking-normal">No weather or event impacts found.</div>; {/* Themed */}

  return (
    <div className="p-4 bg-card rounded-lg border border-primary pixel-drop-shadow"> {/* Removed shadow-lg, added border and pixel-drop-shadow */}
      <h2 className="text-xl font-bold mb-4 text-primary tracking-normal">Weather & Event Impacts</h2> {/* Added text-primary tracking-normal */}
      <div className="space-y-4">
        {impacts.map((impact, idx) => {
          let SeverityIcon = ShieldCheck; // Default to Low/Info
          if (impact.severity === 'High') SeverityIcon = ShieldAlert;
          else if (impact.severity === 'Medium') SeverityIcon = ShieldHalf;

          return (
            <div key={idx} className="p-4 rounded-lg border bg-card border-primary pixel-drop-shadow"> {/* Added pixel-drop-shadow */}
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center"> {/* Wrapper for type icon and text */}
                  {impact.type === 'weather' && <CloudSun className="h-5 w-5 mr-2 text-primary flex-shrink-0" />}
                  {impact.type === 'event' && <Megaphone className="h-5 w-5 mr-2 text-primary flex-shrink-0" />}
                  <span className="font-semibold capitalize text-primary tracking-normal">{impact.type}</span> {/* Added text-primary tracking-normal */}
                </div>
                <span className="px-2 py-1 rounded text-sm bg-primary text-primary-foreground tracking-normal flex items-center"> {/* Added flex items-center */}
                  <SeverityIcon className="h-3 w-3 mr-1.5" /> {/* Icon color inherited (text-primary-foreground) */}
                  {impact.severity} Impact
                </span>
              </div>
              <p className="text-primary tracking-normal">{impact.description}</p> {/* Changed color, added tracking */}
              <div className="mt-2 text-sm text-muted-foreground tracking-normal space-y-1"> {/* Changed color, added tracking */}
              <p className="tracking-normal">Location: {impact.location}</p> {/* Added tracking-normal to inner p's for consistency */}
              <p className="tracking-normal">Start: {new Date(impact.startTime).toLocaleString()}</p>
              {impact.endTime && (
                <p className="tracking-normal">End: {new Date(impact.endTime).toLocaleString()}</p>
              )}
              {impact.type === 'weather' && impact.details && (
                // Inherits text-muted-foreground and tracking-normal from parent div
                <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
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
