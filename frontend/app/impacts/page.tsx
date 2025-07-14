import React from 'react';
import WeatherEventImpactPanel from '../../components/WeatherEventImpactPanel';
import { Signal, Clock, BatteryFull } from 'lucide-react';

const ImpactsPage: React.FC = () => (
  <div className="bg-lcd-bg text-lcd-text font-lcd flex flex-col min-h-screen w-full">
    {/* Status Bar */}
    <header className="flex items-center justify-between px-4 py-1 border-b-2 border-lcd-text">
      <div className="flex items-center space-x-2">
        <Signal size={20} />
        <span className="font-lcd matrix-glow">IMPACTS</span>
      </div>
      <div className="flex items-center space-x-2">
        <Clock size={20} />
        <span className="font-lcd matrix-glow">{new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
        <BatteryFull size={20} />
      </div>
    </header>
    <main className="flex-1 p-8">
      <WeatherEventImpactPanel />
    </main>
  </div>
);

export default ImpactsPage;