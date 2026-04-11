'use client';

import React from 'react';
import { FeedStatusData } from '@/lib/types';
import { Wifi, WifiOff, Activity, AlertTriangle } from 'lucide-react';

interface SurveillanceSummaryProps {
  feeds: FeedStatusData[];
}

const SurveillanceSummary: React.FC<SurveillanceSummaryProps> = ({ feeds }) => {
  const onlineFeeds = feeds.filter(f => f.status === 'running').length;
  const offlineFeeds = feeds.length - onlineFeeds;
  const systemHealth = feeds.length > 0 ? (onlineFeeds / feeds.length) * 100 : 100;

  let healthColor = 'text-green-500';
  if (systemHealth < 75) healthColor = 'text-yellow-500';
  if (systemHealth < 50) healthColor = 'text-red-500';

  return (
    <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
      <div className="matrix-card p-4 flex flex-col justify-between">
        <h3 className="text-sm font-bold uppercase tracking-widest text-lcd-text/60">Total Feeds</h3>
        <p className="text-4xl font-black font-lcd text-lcd-text">{feeds.length}</p>
      </div>
      <div className="matrix-card p-4 flex flex-col justify-between">
        <h3 className="text-sm font-bold uppercase tracking-widest text-lcd-text/60 flex items-center">
          <Wifi className="w-4 h-4 mr-2 text-green-500" /> Online
        </h3>
        <p className="text-4xl font-black font-lcd text-green-500">{onlineFeeds}</p>
      </div>
      <div className="matrix-card p-4 flex flex-col justify-between">
        <h3 className="text-sm font-bold uppercase tracking-widest text-lcd-text/60 flex items-center">
          <WifiOff className="w-4 h-4 mr-2 text-red-500" /> Offline
        </h3>
        <p className="text-4xl font-black font-lcd text-red-500">{offlineFeeds}</p>
      </div>
      <div className="matrix-card p-4 flex flex-col justify-between">
        <h3 className="text-sm font-bold uppercase tracking-widest text-lcd-text/60 flex items-center">
          <Activity className="w-4 h-4 mr-2" /> System Health
        </h3>
        <p className={`text-4xl font-black font-lcd ${healthColor}`}>{systemHealth.toFixed(0)}%</p>
      </div>
    </div>
  );
};

export default SurveillanceSummary;
