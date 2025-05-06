import React, { useEffect } from 'react';

import ThreeGrid from '@/components/CesiumGlobe';
import io from 'socket.io-client';
// Define a basic interface for traffic data (to be updated with actual structure)

const TrafficMap: React.FC = () => {
  useEffect(() => {
    const socket = io('/api/traffic/live');

    socket.on('connect', () => {
      console.log('WebSocket connected!');
    });

    socket.on('trafficUpdate', (data) => {
      console.log('Received traffic update:', data);
      // TODO: Process and display the traffic data on the map
    });

    return () => {
      socket.disconnect();
    };
  }, []);

  return (
    <section className="col-span-2 border border-gray-300 rounded-md">
      <ThreeGrid />
    </section>
  );
};

export default TrafficMap;