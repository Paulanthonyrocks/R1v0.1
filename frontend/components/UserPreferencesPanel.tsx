import React, { useEffect, useState, ChangeEvent } from 'react';

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

  if (loading) return <div>Loading preferences...</div>;
  if (error) return <div className="text-red-500">{error}</div>;
  if (!prefs) return null;

  return (
    <div className="p-4 bg-white rounded shadow max-w-md mx-auto">
      <h2 className="text-xl font-bold mb-4">User Preferences</h2>
      <div className="mb-2">
        <label className="mr-2">
          <input
            type="checkbox"
            name="preferHighways"
            checked={prefs.routePreferences.preferHighways}
            onChange={handleChange}
          />
          Prefer Highways
        </label>
        <label className="ml-4">
          <input
            type="checkbox"
            name="preferScenicRoutes"
            checked={prefs.routePreferences.preferScenicRoutes}
            onChange={handleChange}
          />
          Prefer Scenic Routes
        </label>
        <label className="ml-4">
          <input
            type="checkbox"
            name="avoidTolls"
            checked={prefs.routePreferences.avoidTolls}
            onChange={handleChange}
          />
          Avoid Tolls
        </label>
      </div>
      <div className="mb-2">
        <label>
          Preferred Departure Time:
          <input
            type="time"
            name="preferredDepartureTime"
            value={prefs.routePreferences.preferredDepartureTime || ''}
            onChange={handleChange}
            className="ml-2 border rounded px-2"
          />
        </label>
      </div>
      <div className="mb-2">
        <label>
          Traffic Alerts (minutes ahead):
          <input
            type="number"
            name="notifyAheadMinutes"
            value={prefs.trafficAlerts.notifyAheadMinutes}
            onChange={e => setPrefs({ ...prefs, trafficAlerts: { ...prefs.trafficAlerts, notifyAheadMinutes: Number(e.target.value) } })}
            className="ml-2 border rounded px-2 w-16"
          />
        </label>
      </div>
      <div className="mb-2">
        <label>
          Severity Threshold:
          <input
            type="number"
            name="severityThreshold"
            value={prefs.trafficAlerts.severityThreshold}
            onChange={e => setPrefs({ ...prefs, trafficAlerts: { ...prefs.trafficAlerts, severityThreshold: Number(e.target.value) } })}
            className="ml-2 border rounded px-2 w-16"
          />
        </label>
      </div>
      <div className="mb-2">
        <label className="mr-2">
          <input
            type="checkbox"
            name="includeWeather"
            checked={prefs.trafficAlerts.includeWeather}
            onChange={e => setPrefs({ ...prefs, trafficAlerts: { ...prefs.trafficAlerts, includeWeather: e.target.checked } })}
          />
          Include Weather
        </label>
        <label className="ml-4">
          <input
            type="checkbox"
            name="includeEvents"
            checked={prefs.trafficAlerts.includeEvents}
            onChange={e => setPrefs({ ...prefs, trafficAlerts: { ...prefs.trafficAlerts, includeEvents: e.target.checked } })}
          />
          Include Events
        </label>
      </div>
      <button className="mt-4 px-4 py-2 bg-blue-600 text-white rounded" onClick={handleSave} disabled={loading}>
        Save Preferences
      </button>
    </div>
  );
};

export default UserPreferencesPanel;
