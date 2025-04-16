"use client";
import React, { useRef, useEffect } from 'react';
import * as Cesium from 'cesium';

const CesiumGlobe: React.FC = () => {
    const containerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (containerRef.current) {
            Cesium.Ion.defaultAccessToken = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJqdGkiOiIxZDU0ZWEwMy01MDY4LTQ3YmMtYjYyOS0yYTFiNGQxOThhMzkiLCJpZCI6Mjk0Nzk5LCJpYXQiOjE3NDQ4NDU1Mjl9.MkmefZLFVCN3H-fYiYoNwogkTvSGaFXBDz2YbuuZIW0';
            Cesium.buildModuleUrl.setBaseUrl('/Cesium/');

            const viewer = new Cesium.Viewer(containerRef.current, {
                imageryProvider: new Cesium.TileMapServiceImageryProvider({
                    url: Cesium.buildModuleUrl('Assets/Textures/NaturalEarthII/'), // Added trailing slash
                }),
                baseLayerPicker: false,
                geocoder: false,
                sceneModePicker: false,
                navigationHelpButton: false,
                homeButton: false,
                animation: false,
                timeline: false,
                fullscreenButton: false,
                vrButton: false,
            });

            // Add any initial globe customization here

            return () => {
                viewer.destroy();
            };
        }
    }, []);

    return <div ref={containerRef} style={{ width: '100%', height: '600px' }} />;
};

export default CesiumGlobe;