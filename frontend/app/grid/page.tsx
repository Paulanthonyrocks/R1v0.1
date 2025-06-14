"use client";

import { UserRole } from "@/lib/auth/roles";
import AuthGuard from "@/components/auth/AuthGuard";
import MatrixCard from '@/components/MatrixCard';
import { useState, useEffect } from 'react';
import TrafficSignalIcon from '@/components/ui/TrafficSignalIcon'; // Import the new icon component
import DitheredTrafficIndicator from '@/components/ui/DitheredTrafficIndicator'; // Import dither component

interface GridItemData {
  id: number;
  label: string;
  trafficFlow: 'low' | 'medium' | 'high'; // Example traffic flow states
  signalStatus: 'green' | 'yellow' | 'red'; // Example signal states
  incidents: number;
  nodeHealth: number;
  details: string;
}

const TrafficGridPage: React.FC = () => {
  const [gridItems, setGridItems] = useState<GridItemData[]>([]);
  const [zoom, setZoom] = useState(1);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      setIsLoading(true);
      // Simulate fetching data
      await new Promise((resolve) => setTimeout(resolve, 1000));

      const initialGridItems: GridItemData[] = Array.from({ length: 9 }, (_, index) => ({
        id: index + 1,
        label: `Section ${index + 1}`,
        trafficFlow: ['low', 'medium', 'high'][Math.floor(Math.random() * 3)] as 'low' | 'medium' | 'high',
        signalStatus: ['green', 'yellow', 'red'][Math.floor(Math.random() * 3)] as 'green' | 'yellow' | 'red',
        incidents: Math.floor(Math.random() * 5),
        nodeHealth: Math.floor(Math.random() * 100),
        details: `Detailed information for section ${index + 1}`,
      }));

      setGridItems(initialGridItems);
      setIsLoading(false);
    };

    fetchData();
  }, []);

  const handleZoomIn = () => setZoom((prevZoom) => prevZoom + 0.1);
  const handleZoomOut = () => setZoom(() => Math.max(0.5, zoom - 0.1));

  const handleGridItemClick = (item: GridItemData) => {
    alert(`Clicked on ${item.label}. Details: ${item.details}`);
  };

  return (
    <AuthGuard requiredRole={UserRole.VIEWER}>
      <div className="p-4 w-full h-screen flex flex-col bg-background tracking-normal">
        {isLoading ? (
          <div className="flex-grow flex items-center justify-center">
            <div className="animate-pulse text-primary text-2xl uppercase tracking-normal">Loading...</div>
          </div>
        ) : (
          <>
            <h1 className="text-2xl font-bold mb-4 uppercase tracking-normal">Grid View</h1>

            <div className="flex mb-4">
              {/* Updated to use theme consistent classes - similar to Button variant="default" but without all button.tsx features */}
              <button className="px-4 py-2 bg-primary text-primary-foreground hover:bg-primary/90 mr-2 tracking-normal" onClick={handleZoomIn}>Zoom In</button>
              <button className="px-4 py-2 bg-primary text-primary-foreground hover:bg-primary/90 tracking-normal" onClick={handleZoomOut}>Zoom Out</button>
            </div>

            <div
              className="grid grid-cols-1 md:grid-cols-3 gap-4 flex-grow overflow-auto"
              style={{
                transform: `scale(${zoom})`,
                transformOrigin: 'top left',
                imageRendering: 'pixelated',
                msImageRendering: 'crisp-edges', // For IE/Edge
                mozImageRendering: 'crisp-edges', // For Firefox (older versions)
              }}
            >
              {gridItems.map((item) => (
                <div key={item.id} onClick={() => handleGridItemClick(item)} style={{ cursor: 'pointer' }}>
                  <MatrixCard
                    title={item.label}
                  >
                    <div className="flex flex-col gap-2 text-center"> {/* Added text-center to parent div */}
                      <p className="text-matrix-muted-text pixel-drop-shadow uppercase font-bold tracking-normal flex items-center justify-center gap-2">
                        Traffic: <DitheredTrafficIndicator flow={item.trafficFlow} width="24px" height="12px" /> {item.trafficFlow}
                      </p>
                      <p className="text-matrix-muted-text pixel-drop-shadow uppercase font-bold tracking-normal flex items-center justify-center gap-2">
                        Signal: <TrafficSignalIcon status={item.signalStatus} /> {item.signalStatus}
                      </p>
                      {/* Incidents and Node Health are not explicitly "critical" in the same way, applying normal tracking and centering only for consistency for now */}
                      <p className="text-matrix-muted-text pixel-drop-shadow tracking-normal">Incidents: {item.incidents}</p>
                      <p className="text-matrix-muted-text pixel-drop-shadow tracking-normal">Node Health: {item.nodeHealth}%</p>
                    </div>
                  </MatrixCard>
                </div>
              ))}
            </div>
          </>
        )}
      </div>
    </AuthGuard>
  );
};

export default TrafficGridPage;