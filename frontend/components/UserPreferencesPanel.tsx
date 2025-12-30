"use client";

import React, { useEffect, useState, ChangeEvent } from 'react';
import { Save, Shield, Map as MapIcon, Bell, Clock, CloudSun, Calendar } from 'lucide-react';
import { Button } from './ui/button';
import { cn } from '@/lib/utils';

interface UserPreferences {
  routePreferences: {
    preferHighways: boolean;
    preferScenicRoutes: boolean;
    avoidTolls: boolean;
    preferredDepartureTime?: string;
    commonDestinations: Array<{
      name: string;
      location: [number, number];
      preferredRoute?: string;
    }>;
  };
  trafficAlerts: {
    notifyAheadMinutes: number;
    severityThreshold: number;
    includeWeather: boolean;
    includeEvents: boolean;
  };
}

const UserPreferencesPanel: React.FC = () => {
  const [prefs, setPrefs] = useState<UserPreferences | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch('/api/v1/user-preferences')
      .then(res => res.json())
      .then(setPrefs)
      .catch(() => setError('Failed to load preferences'))
      .finally(() => setLoading(false));
  }, []);

  const handleChange = (e: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    if (!prefs) return;
    
    if (e.target instanceof HTMLInputElement) {
      const { name, value, type, checked } = e.target;
      setPrefs({
        ...prefs,
        routePreferences: {
          ...prefs.routePreferences,
          [name]: type === 'checkbox' ? checked : value,
        },
      });
    } else {
      const { name, value } = e.target;
      setPrefs({
        ...prefs,
        routePreferences: {
          ...prefs.routePreferences,
          [name]: value,
        },
      });
    }
  };

  const handleSave = async () => {
    setLoading(true);
    setError(null);
    try {
      await fetch('/api/v1/user-preferences', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(prefs),
      });
    } catch {
      setError('Failed to save preferences');
    }
    setLoading(false);
  };

  if (loading && !prefs) return <div className="text-center py-20 uppercase font-bold animate-matrix-pulse text-lcd-bg">Accessing Profile Data...</div>;
  if (error) return <div className="text-red-500 border border-red-500 p-4 uppercase font-bold">{error}</div>;
  if (!prefs) return null;

  return (
    <div className="max-w-4xl mx-auto w-full space-y-8 pb-20">
      <div className="flex justify-between items-center border-b-2 border-lcd-bg/20 pb-4 mb-8">
          <h1 className="text-4xl font-bold uppercase tracking-tighter text-lcd-bg matrix-glow">System Directives</h1>
          <Button 
            onClick={handleSave} 
            disabled={loading}
            className="bg-lcd-bg text-lcd-text hover:bg-white/90 transition-all font-bold uppercase tracking-widest rounded-none h-12 px-8"
          >
            <Save size={18} className="mr-2" /> Save Config
          </Button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
          {/* Route Preferences */}
          <div className="matrix-card p-6">
              <h2 className="text-xl font-bold uppercase mb-6 flex items-center gap-2 text-lcd-text">
                  <MapIcon size={20} /> Navigation Logic
              </h2>
              <div className="space-y-6">
                  <div className="flex flex-col gap-4 text-lcd-text">
                      <PreferenceToggle 
                        label="Prioritize Highways" 
                        name="preferHighways" 
                        checked={prefs.routePreferences.preferHighways} 
                        onChange={handleChange} 
                      />
                      <PreferenceToggle 
                        label="Prioritize Scenic" 
                        name="preferScenicRoutes" 
                        checked={prefs.routePreferences.preferScenicRoutes} 
                        onChange={handleChange} 
                      />
                      <PreferenceToggle 
                        label="Avoid Toll Corridors" 
                        name="avoidTolls" 
                        checked={prefs.routePreferences.avoidTolls} 
                        onChange={handleChange} 
                      />
                  </div>
                  
                  <div className="pt-4 border-t border-lcd-text/10">
                      <label className="text-[10px] uppercase font-bold opacity-60 block mb-2 text-lcd-text">Default Departure Window</label>
                      <div className="relative">
                          <Clock className="absolute left-3 top-1/2 -translate-y-1/2 opacity-40 text-lcd-text" size={16} />
                          <input
                            type="time"
                            name="preferredDepartureTime"
                            value={prefs.routePreferences.preferredDepartureTime || ''}
                            onChange={handleChange}
                            className="bg-lcd-text/5 border-2 border-lcd-text/20 text-lcd-text p-2 pl-10 w-full focus:outline-none focus:border-lcd-text font-lcd font-bold uppercase"
                          />
                      </div>
                  </div>
              </div>
          </div>

          {/* Alert Preferences */}
          <div className="matrix-card p-6">
              <h2 className="text-xl font-bold uppercase mb-6 flex items-center gap-2 text-lcd-text">
                  <Bell size={20} /> Alert Protocols
              </h2>
              <div className="space-y-6">
                  <div className="grid grid-cols-2 gap-4">
                      <div className="space-y-2">
                          <label className="text-[10px] uppercase font-bold opacity-60 block text-lcd-text">Notification Lead (Min)</label>
                          <input
                            type="number"
                            value={prefs.trafficAlerts.notifyAheadMinutes}
                            onChange={e => setPrefs({ ...prefs, trafficAlerts: { ...prefs.trafficAlerts, notifyAheadMinutes: Number(e.target.value) } })}
                            className="bg-lcd-text/5 border-2 border-lcd-text/20 text-lcd-text p-2 w-full focus:outline-none focus:border-lcd-text font-lcd font-bold"
                          />
                      </div>
                      <div className="space-y-2">
                          <label className="text-[10px] uppercase font-bold opacity-60 block text-lcd-text">Severity Cutoff (1-5)</label>
                          <input
                            type="number"
                            min="1"
                            max="5"
                            value={prefs.trafficAlerts.severityThreshold}
                            onChange={e => setPrefs({ ...prefs, trafficAlerts: { ...prefs.trafficAlerts, severityThreshold: Number(e.target.value) } })}
                            className="bg-lcd-text/5 border-2 border-lcd-text/20 text-lcd-text p-2 w-full focus:outline-none focus:border-lcd-text font-lcd font-bold"
                          />
                      </div>
                  </div>

                  <div className="pt-4 space-y-4 text-lcd-text">
                      <label className="text-[10px] uppercase font-bold opacity-60 block mb-2">Environmental Subscriptions</label>
                      <PreferenceToggle 
                        label="Atmospheric Impacts" 
                        name="includeWeather" 
                        icon={<CloudSun size={14} />}
                        checked={prefs.trafficAlerts.includeWeather} 
                        onChange={(e) => setPrefs({ ...prefs, trafficAlerts: { ...prefs.trafficAlerts, includeWeather: (e.target as HTMLInputElement).checked } })} 
                      />
                      <PreferenceToggle 
                        label="Public Events" 
                        name="includeEvents" 
                        icon={<Calendar size={14} />}
                        checked={prefs.trafficAlerts.includeEvents} 
                        onChange={(e) => setPrefs({ ...prefs, trafficAlerts: { ...prefs.trafficAlerts, includeEvents: (e.target as HTMLInputElement).checked } })} 
                      />
                  </div>
              </div>
          </div>
      </div>

      <div className="matrix-card p-6 border-dashed opacity-60">
          <div className="flex gap-4 items-center">
              <Shield size={32} className="text-lcd-text opacity-50 shrink-0" />
              <div>
                  <h3 className="font-bold uppercase text-sm mb-1 text-lcd-text">Data Integrity Notice</h3>
                  <p className="text-xs leading-relaxed text-lcd-text">
                      Preferences are stored locally and synchronized with the central hub. 
                      Changes may take up to 60 seconds to propagate to the edge processing nodes.
                  </p>
              </div>
          </div>
      </div>
    </div>
  );
};

const PreferenceToggle = ({ label, name, checked, onChange, icon }: { label: string, name: string, checked: boolean, onChange: (e: ChangeEvent<HTMLInputElement>) => void, icon?: React.ReactNode }) => (
    <label className="flex items-center justify-between cursor-pointer group p-2 hover:bg-lcd-text/5 transition-colors">
        <span className="flex items-center gap-2 text-sm uppercase font-bold tracking-wide">
            {icon} {label}
        </span>
        <div className="relative">
            <input
                type="checkbox"
                name={name}
                checked={checked}
                onChange={onChange}
                className="sr-only"
            />
            <div className={cn("w-10 h-5 bg-lcd-text/20 transition-colors rounded-none border border-lcd-text/40", checked && "bg-lcd-text border-lcd-text")}></div>
            <div className={cn("absolute left-1 top-1 w-3 h-3 bg-lcd-text/60 transition-transform", checked && "translate-x-5 bg-white")}></div>
        </div>
    </label>
);

export default UserPreferencesPanel;