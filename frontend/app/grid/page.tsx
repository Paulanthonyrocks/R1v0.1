import React from 'react';
import ThreeGrid from '@/components/CesiumGlobe';

const TrafficGridPage: React.FC = () => {
  return (
    <div>
      <h1>Traffic Grid</h1>
      <ThreeGrid />
      {/* Add other traffic grid content here */}
    </div>
  );
};

export default TrafficGridPage;