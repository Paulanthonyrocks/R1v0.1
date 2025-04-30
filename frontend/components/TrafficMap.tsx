import React from 'react';
import ThreeGrid from '@/components/CesiumGlobe';

const TrafficMap: React.FC = () => {
  return (
    <section className="col-span-2 border border-gray-300 rounded-md">
      <ThreeGrid />
    </section>
  );
};

export default TrafficMap;