import React, { useEffect, useRef, useState } from 'react';
import L from 'leaflet';
import 'leaflet/dist/leaflet.css';
import { cn } from '@/lib/utils'; // Adjust the import based on your project structure
import { usePathname } from 'next/navigation';
import styles from '@/styles/Map.module.css';

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
  // AI-enhanced fields
  prediction_confidence?: number;
  predicted_congestion?: number;
  predicted_time?: string;
  congestion_reason?: string;
  recommended_lane?: number;
  alternate_routes?: {
    route_id: string;
    eta: number;
    congestion_level: number;
    user_preference_score: number;
    description: string;
  }[];
}

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

const MapComponent = () => { // Renamed to avoid conflict with built-in Map type
  const mapContainerRef = useRef<HTMLDivElement | null>(null); // Ref for the map container div
  const mapInstanceRef = useRef<L.Map | null>(null); // Ref to store the map instance
  const pathname = usePathname();
  const [userPreferences, setUserPreferences] = useState<UserPreferences | null>(null);
  const [predictionOverlay, setPredictionOverlay] = useState<L.LayerGroup | null>(null);

  // Function to fetch user preferences
  const fetchUserPreferences = async () => {
    try {
      const response = await fetch('/api/v1/user-preferences');
      if (response.ok) {
        const prefs = await response.json();
        setUserPreferences(prefs);
      }
    } catch (error) {
      console.error('Error fetching user preferences:', error);
    }
  };

  // Function to get AI traffic predictions
  const fetchTrafficPredictions = async () => {
    if (!mapInstanceRef.current) return;
    
    try {
      const response = await fetch('/api/v1/traffic-predictions');
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      const predictions = await response.json();
      
      // Clear existing prediction overlay
      if (predictionOverlay && mapInstanceRef.current) {
        predictionOverlay.clearLayers();
      }

      // Create new overlay for predictions
      const newOverlay = L.layerGroup().addTo(mapInstanceRef.current);

      predictions.forEach((pred: TrafficDataItem) => {
        const color = getPredictionColor(pred.predicted_congestion || 0);
        const circle = L.circle([pred.latitude, pred.longitude], {
          color: color,
          fillColor: color,
          fillOpacity: 0.3,
          radius: 100
        });

        // Create detailed popup content
        const popupContent = `
          <div class="prediction-popup">
            <h3>Traffic Prediction</h3>
            <p>Congestion Level: ${pred.predicted_congestion}</p>
            <p>Confidence: ${pred.prediction_confidence}%</p>
            ${pred.congestion_reason ? `<p>Reason: ${pred.congestion_reason}</p>` : ''}
            ${pred.alternate_routes ? createAlternateRoutesHTML(pred.alternate_routes) : ''}
          </div>
        `;

        circle.bindPopup(popupContent);
        circle.addTo(newOverlay);
      });

      setPredictionOverlay(newOverlay);
    } catch (error) {
      console.error('Error fetching traffic predictions:', error);
    }
  };

  // Helper function to determine color based on predicted congestion
  const getPredictionColor = (congestion: number): string => {
    if (congestion > 0.8) return '#ff0000';
    if (congestion > 0.6) return '#ff6600';
    if (congestion > 0.4) return '#ffcc00';
    return '#00cc00';
  };

  // Helper function to create HTML for alternate routes
  const createAlternateRoutesHTML = (routes: TrafficDataItem['alternate_routes']) => {
    if (!routes) return '';
    return `
      <div class="alternate-routes">
        <h4>Alternative Routes:</h4>
        ${routes.map(route => `
          <div class="route-option">
            <p>ETA: ${route.eta} min</p>
            <p>Congestion: ${route.congestion_level}%</p>
            <p>${route.description}</p>
          </div>
        `).join('')}
      </div>
    `;
  };

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

      // Fetch user preferences
      fetchUserPreferences();

      // Set up periodic updates for predictions
      const predictionInterval = setInterval(fetchTrafficPredictions, 300000); // Update every 5 minutes

      // Cleanup function
      return () => {
        clearInterval(predictionInterval);
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
  return (
    <main
      className={cn(
        // ...other classes...
        pathname !== '/' && 'pt-16'
      )}
    >
      <div ref={mapContainerRef} className={styles.mapContainer} />
    </main>
  );
};

export default MapComponent;