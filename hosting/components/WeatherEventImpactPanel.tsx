"use client";
import React, { useEffect, useState } from 'react';
import { CloudSun, MapPin, Calendar, Wind, Droplets, Thermometer, Zap } from 'lucide-react';
import { cn } from '@/lib/utils';
import {
  environmentService,
  type WeatherImpact,
  type EventImpact,
} from '@/lib/services/environmentService';

// The backend formats both lanes into the same impact-card shape
// (weather.py /impact + events.py /impacts); severity is scored server-side.
type ImpactItem = WeatherImpact | EventImpact;

const WeatherEventImpactPanel: React.FC = () => {
  const [impacts, setImpacts] = useState<ImpactItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchData = async () => {
      const defaultLat = 34.0522;
      const defaultLon = -118.2437;

      const [weatherRes, eventsRes] = await Promise.allSettled([
        environmentService.getWeatherImpact(defaultLat, defaultLon),
        environmentService.getEventImpacts(),
      ]);

      const nextImpacts: ImpactItem[] = [];
      let anySucceeded = false;

      if (weatherRes.status === 'fulfilled' && weatherRes.value) {
        nextImpacts.push(weatherRes.value);
        anySucceeded = true;
      }

      if (eventsRes.status === 'fulfilled' && Array.isArray(eventsRes.value)) {
        nextImpacts.push(...eventsRes.value);
        anySucceeded = true;
      }

      if (anySucceeded) {
        setImpacts(nextImpacts);
      } else {
        setError('Failed to retrieve environmental impact telemetry — weather and event links both down');
      }
      setLoading(false);
    };

    fetchData();
  }, []);

  if (loading) return <div className="text-center py-20 uppercase font-bold animate-pulse tracking-widest opacity-50">Polling Environmental Sensors...</div>;
  if (error) return (
      <div className="p-10 text-red-500 border-2 border-red-500 flex flex-col items-center gap-4">
          <Zap className="h-12 w-12" />
          <p className="font-bold uppercase tracking-widest text-center">{error}</p>
      </div>
  );
  if (impacts.length === 0) return (
      <div className="p-10 border-2 border-lcd-text/20 flex flex-col items-center gap-4 text-lcd-text/50">
          <CloudSun className="h-12 w-12" />
          <p className="font-bold uppercase tracking-widest text-center">No active weather or event impacts detected</p>
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
                            <MapPin size={14} className="text-primary" /> {impact.location || 'Unknown'}
                        </div>
                        <div className="flex items-center gap-2 opacity-70 uppercase font-bold">
                            <Calendar size={14} className="text-primary" /> {(() => {
                                if (!impact.startTime) return 'N/A';
                                const d = new Date(impact.startTime);
                                return isNaN(d.getTime()) ? 'N/A' : d.toLocaleString();
                            })()}
                        </div>
                    </div>

                    {impact.type === 'weather' && (
                        <div className="pt-4 border-t border-lcd-text/10 flex flex-wrap gap-6">
                            <WeatherSubStat icon={<Wind size={14} />} label="Wind" value={`${Number(impact.details.wind_speed ?? 0).toFixed(1)} km/h`} />
                            <WeatherSubStat icon={<Droplets size={14} />} label="Precip" value={`${impact.details.precipitation_chance ?? 0}%`} />
                            <WeatherSubStat icon={<Thermometer size={14} />} label="Temp" value={`${impact.details.temperature ?? 0}°C`} />
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
