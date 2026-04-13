"use client";

import React from 'react';
import { SurveillanceFeedMessage } from '@/lib/types';
import { cn } from "@/lib/utils";
import { Users, Zap, Activity } from 'lucide-react';

interface MetricsPanelProps {
    metrics: SurveillanceFeedMessage | null;
    isLive: boolean;
    className?: string;
}

const MetricsPanel: React.FC<MetricsPanelProps> = ({ metrics, isLive, className }) => {
    if (!metrics || !isLive) return null;

    const congestionIndex = metrics.congestion_index || metrics.congestion_level_percent || 0;
    const avgSpeed = metrics.session_average_speed_kmh || metrics.average_speed_kmh || 0;
    const vehicleCount = metrics.total_vehicles_cumulative || metrics.total_vehicles || 0;

    return (
        <div className={cn(
            "relative p-3 bg-industrial-panel backdrop-blur-md border border-lcd-text/30 font-lcd text-lcd-text space-y-3 shadow-[inset_0_0_20px_rgba(0,0,0,0.5)]",
            "rounded-none",
            className
        )}>
            {/* Tactical Corner Accents */}
            <div className="absolute top-0 left-0 w-1 h-1 border-t-2 border-l-2 border-lcd-text/50" />
            <div className="absolute top-0 right-0 w-1 h-1 border-t-2 border-r-2 border-lcd-text/50" />
            <div className="absolute bottom-0 left-0 w-1 h-1 border-b-2 border-l-2 border-lcd-text/50" />
            <div className="absolute bottom-0 right-0 w-1 h-1 border-b-2 border-r-2 border-lcd-text/50" />

            {/* Header */}
            <div className="flex justify-between items-center border-b border-lcd-text/20 pb-1 mb-2">
                <span className="text-[10px] font-black uppercase tracking-[0.2em] opacity-70">Telemetry Data</span>
                <div className="flex items-center gap-1">
                    <div className="h-1.5 w-1.5 rounded-full bg-lcd-green animate-pulse" />
                    <span className="text-[8px] font-bold uppercase tracking-widest text-lcd-green">Live</span>
                </div>
            </div>

            {/* Main Stats */}
            <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1">
                    <div className="flex items-center gap-1.5 opacity-60">
                        <Users size={10} className="text-lcd-green" />
                        <span className="text-[8px] font-bold uppercase tracking-tighter">Flow Volume</span>
                    </div>
                    <div className="text-xl font-black tracking-tighter">
                        {vehicleCount}
                        <span className="text-[10px] ml-1 opacity-40 font-bold">VEH</span>
                    </div>
                </div>

                <div className="space-y-1">
                    <div className="flex items-center gap-1.5 opacity-60">
                        <Zap size={10} className="text-lcd-green" />
                        <span className="text-[8px] font-bold uppercase tracking-tighter">Mean Speed</span>
                    </div>
                    <div className="text-xl font-black tracking-tighter">
                        {avgSpeed.toFixed(1)}
                        <span className="text-[10px] ml-1 opacity-40 font-bold">KM/H</span>
                    </div>
                </div>
            </div>

            {/* Congestion Gauge */}
            <div className="space-y-1.5 pt-1">
                <div className="flex justify-between items-center">
                    <div className="flex items-center gap-1.5 opacity-60">
                        <Activity size={10} className="text-lcd-green" />
                        <span className="text-[8px] font-bold uppercase tracking-tighter">Saturation Level</span>
                    </div>
                    <span className={cn(
                        "text-[10px] font-black tracking-widest",
                        congestionIndex > 70 ? "text-red-500" : 
                        congestionIndex > 40 ? "text-yellow-500" : "text-lcd-green"
                    )}>
                        {congestionIndex.toFixed(1)}%
                    </span>
                </div>
                
                <div className="h-1.5 w-full bg-black/40 border border-lcd-text/20 overflow-hidden relative">
                    <div
                        className={cn(
                            "h-full transition-all duration-1000 ease-out",
                            congestionIndex > 70 ? "bg-red-500 shadow-[0_0_10px_rgba(239,68,68,0.5)]" :
                            congestionIndex > 40 ? "bg-yellow-500 shadow-[0_0_10px_rgba(234,179,8,0.5)]" : 
                            "bg-lcd-green shadow-[0_0_10px_rgba(182,255,176,0.5)]"
                        )}
                        style={{ width: `${Math.min(100, congestionIndex)}%` }}
                    />
                </div>
            </div>

            {/* System Note */}
            <div className="text-[7px] opacity-30 uppercase tracking-[0.3em] text-center pt-1 italic">
                Neural Processor // Edge Analytics 
            </div>
        </div>
    );
};

export default MetricsPanel;
