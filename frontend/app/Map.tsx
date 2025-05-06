import React, { useEffect, useRef } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

// It's good practice to define icons if you're using markers,
// though circles don't strictly need this, it avoids some common Leaflet issues.
// If you were using L.marker, you'd need this:
// import iconRetinaUrl from 'leaflet/dist/images/marker-icon-2x.png';
// import iconUrl from 'leaflet/dist/images/marker-icon.png';
// import shadowUrl from 'leaflet/dist/images/marker-shadow.png';

// L.Icon.Default.mergeOptions({
//   iconRetinaUrl,
//   iconUrl,
//   shadowUrl,
// });


interface TrafficDataItem {
  latitude: number;
  longitude: number;
  vehicle_count: number;
  // You might also want sensor_id, timestamp, congestion_score etc.
  // depending on what your API returns and what you want to display.
  // sensor_id?: string;
  // timestamp?: number;
  // congestion_score?: number;
}

const MapComponent = () => { // Renamed to avoid conflict with built-in Map type
  const mapContainerRef = useRef<HTMLDivElement | null>(null); // Ref for the map container div
  const mapInstanceRef = useRef<L.Map | null>(null); // Ref to store the map instance

  useEffect(() => {
    // Ensure the map container is available and map isn't already initialized
    if (mapContainerRef.current && !mapInstanceRef.current) {
      // Initialize the map
      const map = L.map(mapContainerRef.current).setView([51.505, -0.09], 13);
      mapInstanceRef.current = map;

      // Add OpenStreetMap tiles
      L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
      }).addTo(map); // Add to the local 'map' variable, which is guaranteed to be non-null here

      // Fetch and display traffic data
      const fetchTrafficData = async () => {
        if (!mapInstanceRef.current) return; // Guard against map not being initialized

        try {
          const response = await fetch('/api/v1/traffic-data'); // Ensure this endpoint exists and returns data
          if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
          }
          const data: TrafficDataItem[] = await response.json();

          data.forEach((item) => {
            const { latitude, longitude, vehicle_count } = item;
            if (mapInstanceRef.current) { // Explicitly check mapInstanceRef.current before using
              L.circle([latitude, longitude], {
                color: vehicle_count > 50 ? 'orange' : (vehicle_count > 20 ? 'yellow' : 'green'), // Example: color based on count
                fillColor: vehicle_count > 50 ? '#f03' : (vehicle_count > 20 ? '#f90' : '#0f0'),
                fillOpacity: 0.5,
                radius: vehicle_count * 10 + 50 // Adjust radius: base size + increment per vehicle
              }).addTo(mapInstanceRef.current).bindPopup(`Vehicles: ${vehicle_count}`);
            }
          });
        } catch (error) {
          console.error('Error fetching or processing traffic data:', error);
        }
      };

      // Initial data fetch
      fetchTrafficData();

      // Consider adding an interval to refresh data if it's real-time
      // const intervalId = setInterval(fetchTrafficData, 30000); // Fetch every 30 seconds

      // Cleanup function
      return () => {
        // clearInterval(intervalId); // Clear interval if you set one
        if (mapInstanceRef.current) {
          mapInstanceRef.current.remove(); // Dispose of the map on unmount
          mapInstanceRef.current = null; // Clear the ref
        }
      };
    }
  }, []); // Empty dependency array means this effect runs once on mount and cleanup on unmount

  // Note: The div now uses mapContainerRef instead of a direct id="map"
  // This is generally a more React-idiomatic way to handle DOM elements.
  return <div ref={mapContainerRef} style={{ height: '100vh', width: '100%' }} />;
};

export default MapComponent;