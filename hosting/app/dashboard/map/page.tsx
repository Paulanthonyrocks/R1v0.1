"use client";

import React, { useState } from 'react';
import dynamic from 'next/dynamic';
import 'leaflet/dist/leaflet.css';

// Dynamically import LeafletMap to avoid SSR issues as Leaflet requires the 'window' object
const DynamicLeafletMap = dynamic(() => import('@/components/map/LeafletMap'), {
  ssr: false,
  loading: () => (
    <div className="fixed inset-0 bg-industrial-bg text-lcd-green font-lcd flex items-center justify-center z-50">
      <div className="flex flex-col items-center gap-4">
        <div className="w-12 h-12 border-4 border-lcd-green border-t-transparent rounded-full animate-spin" />
        <div className="animate-pulse text-xl uppercase tracking-[0.3em]">Initializing Neural Grid...</div>
      </div>
    </div>
  )
});

const LiveMapPage: React.FC = () => {
  const [activeLayer, setActiveLayer] = useState<'satellite' | 'vector' | 'thermal'>('satellite');

  return (
    <div className="relative w-full h-screen bg-industrial-bg overflow-hidden">
      <DynamicLeafletMap activeLayer={activeLayer} />
    </div>
  );
};

export default LiveMapPage;
