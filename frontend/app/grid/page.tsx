import React from 'react';
import CesiumGlobe from '@/components/CesiumGlobe';

const TrafficGridPage: React.FC = () => {
  return (
    <div>
      <h1>Traffic Grid</h1>
      <CesiumGlobe />
      {/* Add other traffic grid content here */}
    </div>
  );
};

export default TrafficGridPage;