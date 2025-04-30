'use client';

import MatrixCard from '@/components/MatrixCard';
import { useState } from 'react';
import LoadingScreen from '@/components/LoadingScreen';

const SurveillancePage = () => {
  const [loading, setLoading] = useState(false);

  const cameraFeeds = [
    { id: 1, name: 'Camera 1', url: 'https://www.sample-videos.com/video123/mp4/720/big_buck_bunny_720p_1mb.mp4' },
    { id: 2, name: 'Camera 2', url: 'https://www.sample-videos.com/video123/mp4/720/big_buck_bunny_720p_1mb.mp4' },
    { id: 3, name: 'Camera 3', url: 'https://www.sample-videos.com/video123/mp4/720/big_buck_bunny_720p_1mb.mp4' },
    { id: 4, name: 'Camera 4', url: 'https://www.sample-videos.com/video123/mp4/720/big_buck_bunny_720p_1mb.mp4' },
    { id: 5, name: 'Camera 5', url: 'https://www.sample-videos.com/video123/mp4/720/big_buck_bunny_720p_1mb.mp4' },
    { id: 6, name: 'Camera 6', url: 'https://www.sample-videos.com/video123/mp4/720/big_buck_bunny_720p_1mb.mp4' },
    { id: 7, name: 'Camera 7', url: 'https://www.sample-videos.com/video123/mp4/720/big_buck_bunny_720p_1mb.mp4' },
    { id: 8, name: 'Camera 8', url: 'https://www.sample-videos.com/video123/mp4/720/big_buck_bunny_720p_1mb.mp4' },
    { id: 9, name: 'Camera 9', url: 'https://www.sample-videos.com/video123/mp4/720/big_buck_bunny_720p_1mb.mp4' },
  ];

  if (loading) {
    return <LoadingScreen />;
  }

  return (
    <div className="p-4 w-full relative">
      <h1 className="text-2xl font-bold mb-4 uppercase">SURVEILLANCE</h1>

      <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4 ">
        {cameraFeeds.map((camera) => (
          <MatrixCard key={camera.id} className="p-4 flex flex-col">
            <h2 className="text-lg font-bold mb-2">{camera.name}</h2>
            <div className="flex-grow relative">
              <video
                className="w-full h-full object-cover"
                controls
                src={camera.url}
              />
              <div className="absolute top-2 right-2">
                {/* Placeholder for alerts */}
                <span className="bg-red-500 text-white text-xs font-bold px-2 py-1 rounded">Alert</span>
              </div>
              {/* Placeholder for controls */}
              <div className="absolute bottom-2 right-2">Controls</div>
            </div>
          </MatrixCard>
        ))}
      </div>
    </div>
  );
};


export default SurveillancePage;