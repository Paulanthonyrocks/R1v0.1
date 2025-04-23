"use client";
import React, { useRef, useEffect } from 'react';
import * as Cesium from 'cesium';

// It's often necessary to copy Cesium assets (Workers, Assets, Widgets, ThirdParty)
// to your public directory (e.g., public/Cesium) and configure your build tool
// or set window.CESIUM_BASE_URL = '/Cesium/'; before initializing the viewer.
// The buildModuleUrl.setBaseUrl method is deprecated or used differently now.

const CesiumGlobe: React.FC<Record<never, never>> = () => {
    const containerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        let viewer: Cesium.Viewer | undefined;
        
        const initViewer = async () => {
            if (containerRef.current) {
                Cesium.Ion.defaultAccessToken = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJqdGkiOiIxZDU0ZWEwMy01MDY4LTQ3YmMtYjYyOS0yYTFiNGQxOThhMzkiLCJpZCI6Mjk0Nzk5LCJpYXQiOjE3NDQ4NDU1Mjl9.MkmefZLFVCN3H-fYiYoNwogkTvSGaFXBDz2YbuuZIW0';
                const viewerOptions: Cesium.Viewer.ConstructorOptions = {
                    // The type definitions might be slightly off. Cast if necessary,
                    // but ensure the structure matches Cesium documentation.
                    baseLayer: new Cesium.ImageryLayer(
                        await Cesium.TileMapServiceImageryProvider.fromUrl(
                            '/Cesium/Assets/Textures/NaturalEarthII'
                        )
                    ),
                    baseLayerPicker: false,
                    geocoder: false,
                    sceneModePicker: false,
                    navigationHelpButton: false,
                    homeButton: false,
                    animation: false,
                    timeline: false,
                    fullscreenButton: false,
                    vrButton: false,
                };

                viewer = new Cesium.Viewer(containerRef.current, viewerOptions);
            }
        };

        initViewer();

        return () => {
            if (viewer) {
                viewer.destroy();
            }
        };
    }, []);

    return <div ref={containerRef} style={{ width: '100%', height: '600px' }} />;
};

export default CesiumGlobe;