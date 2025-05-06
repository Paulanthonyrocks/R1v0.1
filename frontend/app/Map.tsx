import React, { useEffect } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

const Map = () => {
  useEffect(() => {
    // Initialize the map
    const map = L.map('map').setView([51.505, -0.09], 13);

    // Add OpenStreetMap tiles
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(map);

    // Fetch and display traffic data
    const fetchTrafficData = async () => {
      try {
        const response = await fetch('/api/v1/traffic-data');
        const data = await response.json();

        data.forEach((item) => {
          const { latitude, longitude, vehicle_count } = item;
          L.circle([latitude, longitude], {
            color: 'red',
            radius: vehicle_count * 10
          }).addTo(map).bindPopup(`Vehicles: ${vehicle_count}`);
        });
      } catch (error) {
        console.error('Error fetching traffic data:', error);
      }
    };

    fetchTrafficData();
  }, []);

  return <div id="map" style={{ height: '100vh', width: '100%' }}></div>;
};

export default Map;