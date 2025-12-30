"use client";
import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { CloudSun, MapPin, Calendar, Wind, Droplets, Thermometer, Zap } from 'lucide-react';
import { cn } from '@/lib/utils';

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
        const defaultLat = 34.0522;
        const defaultLon = -118.2437;

        const [weatherResponse, eventsResponse] = await Promise.all([
          axios.get(`/api/v1/weather/current?lat=${defaultLat}&lon=${defaultLon}`),
          axios.get('/api/v1/events/current')
        ]);

        const weatherData = weatherResponse.data;
        const eventsData = eventsResponse.data;

        const weatherImpact: WeatherEventImpact = {
          type: 'weather',
          description: `${weatherData.conditions} - ${weatherData.temperature}°C`,
          severity: getSeverityFromWeather(weatherData),
          location: 'Regional Hub',
          startTime: new Date().toISOString(),
          details: weatherData
        };

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
        setError('Failed to retrieve atmospheric and event telemetry');
        console.error('Error fetching impacts:', err);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  const getSeverityFromWeather = (weather: WeatherData): string => {
    if (weather.precipitation_chance > 70 || weather.wind_speed > 50) return 'High';
    if (weather.precipitation_chance > 30 || weather.wind_speed > 30) return 'Medium';
    return 'Low';
  };

  if (loading) return <div className="text-center py-20 uppercase font-bold animate-pulse tracking-widest opacity-50">Polling Environmental Sensors...</div>;
  if (error) return (
      <div className="p-10 text-red-500 border-2 border-red-500 flex flex-col items-center gap-4">
          <Zap className="h-12 w-12" />
          <p className="font-bold uppercase tracking-widest text-center">{error}</p>
      </div>
  );

  return (
    <div className="max-w-4xl mx-auto w-full space-y-8">
      <div className="border-b-2 border-lcd-text pb-4 mb-8">
          <h1 className="text-4xl font-bold uppercase tracking-tighter flex items-center gap-3">
              <CloudSun size={32} className="text-primary" /> Environmental Impacts
          </h1>
          <p className="text-lcd-text/60 mt-2">External factors influencing network throughput and safety protocols.</p>
      </div>

      <div className="grid grid-cols-1 gap-6">
        {impacts.map((impact, idx) => (
          <div key={idx} className={cn(
              "matrix-card overflow-hidden",
              impact.severity === 'High' && "border-red-500/50 bg-red-500/5",
              impact.severity === 'Medium' && "border-yellow-500/50 bg-yellow-500/5"
          )}>
            <div className="p-6 flex flex-col md:flex-row gap-6">
                <div className="shrink-0 flex flex-col items-center justify-center w-24 h-24 bg-lcd-text/10 rounded border border-lcd-text/20">
                    <span className="text-[10px] font-bold uppercase opacity-60 mb-1">{impact.type}</span>
                    {impact.type === 'weather' ? <CloudSun size={32} /> : <Zap size={32} />}
                </div>

                <div className="flex-1 space-y-4">
                    <div className="flex flex-col md:flex-row md:items-center justify-between gap-2">
                        <h3 className="text-xl font-bold uppercase tracking-tight">{impact.description}</h3>
                        <span className={cn(
                            "px-3 py-1 text-[10px] font-bold uppercase tracking-widest border shrink-0 w-fit",
                            impact.severity === 'High' ? "bg-red-500 text-black border-red-500" :
                            impact.severity === 'Medium' ? "bg-yellow-500 text-black border-yellow-500" :
                            "bg-green-500 text-black border-green-500"
                        )}>
                            {impact.severity} Severity Impact
                        </span>
                    </div>

                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
                        <div className="flex items-center gap-2 opacity-70 uppercase font-bold">
                            <MapPin size={14} className="text-primary" /> {impact.location}
                        </div>
                        <div className="flex items-center gap-2 opacity-70 uppercase font-bold">
                            <Calendar size={14} className="text-primary" /> {new Date(impact.startTime).toLocaleString()}
                        </div>
                    </div>

                    {impact.type === 'weather' && impact.details && (
                        <div className="pt-4 border-t border-lcd-text/10 flex flex-wrap gap-6">
                            <WeatherSubStat icon={<Wind size={14} />} label="Wind" value={`${(impact.details as WeatherData).wind_speed.toFixed(1)} km/h`} />
                            <WeatherSubStat icon={<Droplets size={14} />} label="Precip" value={`${(impact.details as WeatherData).precipitation_chance}%`} />
                            <WeatherSubStat icon={<Thermometer size={14} />} label="Temp" value={`${(impact.details as WeatherData).temperature}°C`} />
                        </div>
                    )}
                </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

const WeatherSubStat = ({ icon, label, value }: { icon: React.ReactNode, label: string, value: string }) => (
    <div className="flex items-center gap-2">
        <span className="opacity-40">{icon}</span>
        <span className="text-[10px] uppercase font-bold opacity-60 mr-1">{label}:</span>
        <span className="text-xs font-bold font-lcd">{value}</span>
    </div>
);

export default WeatherEventImpactPanel;
