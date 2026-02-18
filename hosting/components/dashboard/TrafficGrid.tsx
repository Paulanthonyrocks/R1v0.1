// TrafficGrid.tsx
import React, { useEffect, useRef } from 'react';
import * as Cesium from 'cesium';
import 'cesium/Build/Cesium/Widgets/widgets.css';

interface GlobeProps {
  initialPosition?: {
    longitude: number;
    latitude: number;
    height: number;
  };
  initialHeading?: number;
  initialPitch?: number;
  initialRoll?: number;
}

const Globe: React.FC<GlobeProps> = ({
  initialPosition = { longitude: -74.018, latitude: 40.708, height: 100000 },
  initialHeading = 0.0,
  initialPitch = -Cesium.Math.PI_OVER_TWO,
  initialRoll = 0.0,
}) => {
  const globeRef = useRef<HTMLDivElement>(null);
  const viewerRef = useRef<Cesium.Viewer | null>(null); // Keep a ref to the viewer for cleanup

  useEffect(() => {
    if (globeRef.current && !viewerRef.current) { // Initialize only once
      const viewer = new Cesium.Viewer(globeRef.current, {
        // Viewer options... (keep as they were)
        animation: false,
        terrainProviderViewModels: [],
        imageryProviderViewModels: [],
        selectedImageryProviderViewModel: undefined,
        selectedTerrainProviderViewModel: undefined,
        baseLayerPicker: false,
        fullscreenButton: false,
        geocoder: false,
        homeButton: false,
        infoBox: false,
        sceneModePicker: false,
        selectionIndicator: false,
        timeline: false,
        navigationHelpButton: false,
        navigationInstructionsInitiallyVisible: false,
        // Optional: Add a base imagery layer for context
        // imageryProvider: new Cesium.TileMapServiceImageryProvider({
        //     url: Cesium.buildModuleUrl("Assets/Textures/NaturalEarthII"),
        // }),
      });
      viewerRef.current = viewer; // Store viewer instance

      // --- Terrain ---
      Cesium.createWorldTerrainAsync()
        .then(terrainProvider => {
            if (viewerRef.current) { // Check if viewer still exists
                 viewerRef.current.terrainProvider = terrainProvider;
            }
        })
        .catch(error => {
            console.error("Error creating world terrain:", error);
        });

      // --- Camera Position & Orientation ---
      const initialPositionCartographic = Cesium.Cartographic.fromDegrees(
        initialPosition.longitude,
        initialPosition.latitude,
        initialPosition.height
      );

      // --- FIX APPLIED HERE ---
      const initialPositionCartesian = Cesium.Ellipsoid.WGS84.cartographicToCartesian(initialPositionCartographic);
      // -----------------------

      const initialOrientation = {
        heading: Cesium.Math.toRadians(initialHeading),
        pitch: initialPitch,
        roll: initialRoll,
      };

      viewer.camera.setView({
        destination: initialPositionCartesian,
        orientation: initialOrientation,
      });
    }

    // --- Cleanup function ---
    return () => {
        if (viewerRef.current && !viewerRef.current.isDestroyed()) {
            viewerRef.current.destroy();
            viewerRef.current = null; // Clear the ref
            console.log("Cesium Viewer destroyed.");
        }
    };
  // Only run on mount/unmount, position changes handled differently if needed
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Empty dependency array means run once on mount


  // --- Optional: Effect to update camera if props change ---
  useEffect(() => {
    const viewer = viewerRef.current;
    if (viewer && !viewer.isDestroyed()) {
        const newPositionCartographic = Cesium.Cartographic.fromDegrees(
            initialPosition.longitude,
            initialPosition.latitude,
            initialPosition.height
        );
        const newPositionCartesian = Cesium.Ellipsoid.WGS84.cartographicToCartesian(newPositionCartographic);
        const newOrientation = {
            heading: Cesium.Math.toRadians(initialHeading),
            pitch: initialPitch,
            roll: initialRoll,
        };

        // Use flyTo for a smoother transition if desired
        viewer.camera.flyTo({
            destination: newPositionCartesian,
            orientation: newOrientation,
            duration: 1.0 // Adjust duration as needed
        });
        // Or use setView for an immediate jump (like initially)
        // viewer.camera.setView({
        //     destination: newPositionCartesian,
        //     orientation: newOrientation,
        // });
    }
  }, [initialPosition, initialHeading, initialPitch, initialRoll]); // Re-run if these props change


  return <div className="w-full h-full" ref={globeRef} />;
};


// --- TrafficGrid Component ---
const TrafficGrid: React.FC = () => {
    // Example initial position (optional, could be dynamic)
    const position = { longitude: -74.018, latitude: 40.708, height: 50000 };

    return (
        <div className="w-full h-full relative">
            <div className='absolute top-2 left-2 bg-black/50 text-white p-2 rounded z-10 text-xs'>
                sample_feed
            </div>
            {/* Pass props to Globe */}
            <Globe initialPosition={position}/>
        </div>
    );
};

export default TrafficGrid;