"use client";

import React from 'react';
import { AlertTriangle, TrendingDown, Activity } from 'lucide-react';
import { AlertData } from '@/lib/types';
import AnomalyItem from './AnomalyItem';

interface AnomalyListProps {
    anomalies: AlertData[];
    onSelect?: (anomaly: AlertData) => void;
}

const AnomalyList: React.FC<AnomalyListProps> = ({ anomalies, onSelect }) => {
    return (
        <div className="h-full flex flex-col font-lcd">
            {/* Tactical Log Header */}
            <div className="flex justify-between items-center mb-3 p-2 border border-lcd-green/30 bg-lcd-green/5 relative overflow-hidden group">
                <div className="flex items-center gap-2 text-xs font-bold tracking-widest text-lcd-green uppercase">
                    <Activity className="h-3 w-3 animate-pulse" />
                    <span>Incident_Log.sys</span>
                </div>
                <div className="flex items-center gap-2">
                    <span className="text-[10px] uppercase opacity-50 font-mono">Count:</span>
                    <span className="text-xs font-bold text-lcd-green">{anomalies.length}</span>
                </div>
                {/* Decorative corner accents for the header */}
                <div className="absolute top-0 right-0 w-1 h-1 border-t border-r border-lcd-green/50" />
                <div className="absolute bottom-0 left-0 w-1 h-1 border-b border-l border-lcd-green/50" />
            </div>

            {/* Log Entries Container */}
            <div className="flex-1 overflow-y-auto custom-scrollbar pr-1 space-y-2">
                {anomalies.length === 0 ? (
                    <div className="flex flex-col items-center justify-center py-12 text-center opacity-30">
                        <Activity className="h-8 w-8 mb-2" />
                        <span className="text-xs uppercase tracking-widest">No active incidents detected</span>
                    </div>
                ) : (
                    anomalies.slice().reverse().map((a, i) => (
                        <AnomalyItem 
                            key={a.id || i} 
                            {...a} 
                            onSelect={onSelect} 
                        />
                    ))
                )}
            </div>
        </div>
    );
};

export default AnomalyList;
