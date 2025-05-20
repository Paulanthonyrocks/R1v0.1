'use client';

import MatrixCard from '@/components/MatrixCard';
import { useState } from 'react';
import AuthGuard from '@/components/auth/AuthGuard'; // Import AuthGuard
import { UserRole } from '@/lib/auth/roles'; // Import UserRole
import useSWR from 'swr';

const Loading = () => (
  <div className="fixed inset-0 bg-matrix-bg flex items-center justify-center z-50 top-16">
    <div className="animate-pulse text-matrix text-2xl">Loading...</div>
  </div>
);

const fetcher = (url: string) => fetch(url).then(res => res.json());

const AlertBadge = ({ congestion, incidents }: { congestion: number, incidents: number }) => {
  if (incidents > 0 || congestion > 70) return <span className="bg-red-500 text-white text-xs font-bold px-2 py-1 rounded">Alert</span>;
  if (congestion > 40) return <span className="bg-yellow-400 text-black text-xs font-bold px-2 py-1 rounded">Warning</span>;
  return <span className="bg-green-500 text-white text-xs font-bold px-2 py-1 rounded">OK</span>;
};

const SurveillancePage = () => {
  const [loading,] = useState(false);
  const { data: metrics } = useSWR('/v1/analytics/realtime', fetcher, { refreshInterval: 5000 });
  // In a real application, you would likely fetch data here and set loading based on that.

  const cameraFeeds = [
    { id: 1, name: 'Camera 1', url: '/api/v1/sample-video' },
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
    return <Loading />;
  }

  return (
    <AuthGuard requiredRole={UserRole.OPERATOR}>
      <div className="p-4 w-full relative">
        <h1 className="text-2xl font-bold mb-4 uppercase">SURVEILLANCE</h1>

        <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4 ">
          {cameraFeeds.map((camera) => (
            <MatrixCard key={camera.id} className="p-4 flex flex-col" title="Camera Feed">
              <h2 className="text-lg font-bold mb-2">{camera.name}</h2>
              <div className="flex-grow relative">
                <video
                  className="w-full h-full object-cover"
                  controls
                  src={camera.url}
                />
                <div className="absolute top-2 right-2">
                  {/* Analytics-based alert badge */}
                  <AlertBadge congestion={metrics?.congestion_index ?? 0} incidents={metrics?.active_incidents_count ?? 0} />
                </div>
                {/* Placeholder for controls */}
                <div className="absolute bottom-2 right-2">Controls</div>
              </div>
            </MatrixCard>
          ))}
        </div>
      </div>
    </AuthGuard>
  );
};


export default SurveillancePage;