import React from 'react';
import WeatherEventImpactPanel from '../../components/WeatherEventImpactPanel';

const ImpactsPage: React.FC = () => (
  // Changed bg-gray-50 to bg-background, added tracking-normal
  <main className="min-h-screen bg-background p-8 tracking-normal">
    <WeatherEventImpactPanel />
  </main>
);

export default ImpactsPage;
