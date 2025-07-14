"use client";

import { UserRole } from "@/lib/auth/roles";
import AuthGuard from "@/components/auth/AuthGuard";
import MatrixCard from '@/components/MatrixCard';
import { useState, useEffect } from 'react';
import TrafficSignalIcon from '@/components/ui/TrafficSignalIcon';
import DitheredTrafficIndicator from '@/components/ui/DitheredTrafficIndicator';
import styles from './Grid.module.css';
import { Signal, Clock, BatteryFull } from 'lucide-react';

interface GridItemData {
  id: number;
  label: string;
  trafficFlow: 'low' | 'medium' | 'high';
  signalStatus: 'green' | 'yellow' | 'red';
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

  const renderContent = () => {
    if (isLoading) {
      return (
        <div className="flex-grow flex items-center justify-center">
          <div className="animate-pulse text-lcd-text text-2xl uppercase tracking-normal">Loading...</div>
        </div>
      );
    }

    return (
      <>
        <div className="flex mb-4">
          <button className="px-4 py-2 bg-lcd-text text-lcd-bg hover:bg-lcd-text/90 mr-2 tracking-normal" onClick={handleZoomIn}>Zoom In</button>
          <button className="px-4 py-2 bg-lcd-text text-lcd-bg hover:bg-lcd-text/90 tracking-normal" onClick={handleZoomOut}>Zoom Out</button>
        </div>

        <div
          className={`grid grid-cols-1 md:grid-cols-3 gap-4 flex-grow overflow-auto ${styles.gridZoom}`}
          style={{ transform: `scale(${zoom})` }}
        >
          {gridItems.map((item) => (
            <div key={item.id} onClick={() => handleGridItemClick(item)} className={styles.pointer}>
              <MatrixCard
                title={item.label}
              >
                <div className="flex flex-col gap-2 text-center">
                  <p className="text-lcd-text pixel-drop-shadow uppercase font-bold tracking-normal flex items-center justify-center gap-2">
                    Traffic: <DitheredTrafficIndicator flow={item.trafficFlow} width="24px" height="12px" /> {item.trafficFlow}
                  </p>
                  <p className="text-lcd-text pixel-drop-shadow uppercase font-bold tracking-normal flex items-center justify-center gap-2">
                    Signal: <TrafficSignalIcon status={item.signalStatus} /> {item.signalStatus}
                  </p>
                  <p className="text-lcd-text pixel-drop-shadow tracking-normal">Incidents: {item.incidents}</p>
                  <p className="text-lcd-text pixel-drop-shadow tracking-normal">Node Health: {item.nodeHealth}%</p>
                </div>
              </MatrixCard>
            </div>
          ))}
        </div>
      </>
    );
  };

  return (
    <AuthGuard requiredRole={UserRole.VIEWER}>
      <div className="bg-lcd-bg text-lcd-text font-lcd flex flex-col min-h-screen w-full">
        {/* Status Bar */}
        <header className="flex items-center justify-between px-4 py-1 border-b-2 border-lcd-text">
          <div className="flex items-center space-x-2">
            <Signal size={20} />
            <span className="font-lcd matrix-glow">GRID VIEW</span>
          </div>
          <div className="flex items-center space-x-2">
            <Clock size={20} />
            <span className="font-lcd matrix-glow">{new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
            <BatteryFull size={20} />
          </div>
        </header>
        <main className="flex-1 p-4 flex flex-col">
          <h1 className="text-2xl font-bold mb-4 uppercase tracking-normal">Grid View</h1>
          {renderContent()}
        </main>
      </div>
    </AuthGuard>
  );
};

export default TrafficGridPage;
