'use client';

import React from 'react';
import { FeedStatusData } from '@/lib/types';
import { Wifi, WifiOff, Activity, ShieldAlert } from 'lucide-react';
import { Card } from "@/components/ui/card";
import { cn } from '@/lib/utils';

interface SurveillanceSummaryProps {
  feeds: FeedStatusData[];
}

const SurveillanceSummary: React.FC<SurveillanceSummaryProps> = ({ feeds }) => {
  const onlineFeeds = feeds.filter(f => f.status === 'running').length;
  const offlineFeeds = feeds.length - onlineFeeds;
  const systemHealth = feeds.length > 0 ? (onlineFeeds / feeds.length) * 100 : 100;

  let healthColor = 'text-lcd-green';
  if (systemHealth < 75) healthColor = 'text-yellow-500';
  if (systemHealth < 50) healthColor = 'text-red-500';

  const StatBox = ({ label, value, icon: Icon, colorClass, subtext }: any) => (
    <Card className={cn(
        "relative overflow-hidden p-4 flex flex-col justify-between border-lcd-text/30 bg-industrial-panel text-lcd-text font-lcd",
        "shadow-[inset_0_0_20px_rgba(0,0,0,0.5)] rounded-none"
    )}>
        {/* Tactical Corner Accents */}
        <div className="absolute top-0 left-0 w-1.5 h-1.5 border-t-2 border-l-2 border-lcd-text/40" />
        <div className="absolute top-0 right-0 w-1.5 h-1.5 border-t-2 border-r-2 border-lcd-text/40" />
        <div className="absolute bottom-0 left-0 w-1.5 h-1.5 border-b-2 border-l-2 border-lcd-text/40" />
        <div className="absolute bottom-0 right-0 w-1.5 h-1.5 border-b-2 border-r-2 border-lcd-text/40" />

        <h3 className="text-[10px] font-bold uppercase tracking-widest text-lcd-text/60 flex items-center gap-2 mb-2">
            {Icon && <Icon className={cn("w-3 h-3", colorClass)} />}
            {label}
        </h3>
        <div className="flex items-baseline gap-2">
            <p className={cn("text-3xl font-black font-lcd tracking-tighter", colorClass)}>
                {value}
            </p>
            {subtext && <span className="text-[10px] opacity-40 uppercase">{subtext}</span>}
        </div>
    </Card>
  );

  return (
    <div className="space-y-4 mb-8">
        <div className="flex items-center gap-2 text-xs font-black uppercase tracking-[0.3em] text-lcd-text/40">
            <Activity className="w-3 h-3 animate-pulse text-lcd-green" />
            <span>Network Topology Summary</span>
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <StatBox 
                label="Total Feeds" 
                value={feeds.length} 
                colorClass="text-lcd-text" 
                subtext="NODES"
            />
            <StatBox 
                label="Online" 
                value={onlineFeeds} 
                icon={Wifi} 
                colorClass="text-lcd-green" 
                subtext="ACTIVE"
            />
            <StatBox 
                label="Offline" 
                value={offlineFeeds} 
                icon={WifiOff} 
                colorClass="text-red-500" 
                subtext="SENSORS"
            />
            <StatBox 
                label="System Health" 
                value={`${systemHealth.toFixed(0)}%`} 
                icon={Activity} 
                colorClass={healthColor} 
                subtext="STABILITY"
            />
        </div>
    </div>
  );
};

export default SurveillanceSummary;
