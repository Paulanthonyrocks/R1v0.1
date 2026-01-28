import React from 'react';
import { MatrixCardProps } from '@/lib/types';

const LaneAnalysisWidget: React.FC<MatrixCardProps & {
    occupancy: Record<string, number>,
    queues: Record<string, number>
}> = ({ title, occupancy, queues, children }) => {

    // Convert map to sorted arrays based on keys (Lane 1, Lane 2...)
    const lanes = Object.keys(occupancy).sort();

    return (
        <div className="matrix-card p-6 h-[450px] flex flex-col">
            <div className="flex justify-between items-center mb-6">
                <h2 className="text-xl font-bold tracking-widest font-lcd matrix-glow text-lcd-text">{title}</h2>
                <span className="text-xs text-lcd-text/60 font-lcd">REAL-TIME UTILIZATION</span>
            </div>

            <div className="flex-1 overflow-y-auto custom-scrollbar pr-2 space-y-6">
                {lanes.length === 0 ? (
                    <div className="h-full flex items-center justify-center text-lcd-text/40 text-sm tracking-widest">
                        AWAITING TRAFFIC FLOW...
                    </div>
                ) : (
                    lanes.map(laneId => {
                        const occ = occupancy[laneId] || 0;
                        const queue = queues[laneId] || 0;

                        // Color logic
                        let barColor = "bg-lcd-text";
                        if (occ > 75) barColor = "bg-destructive matrix-glow";
                        else if (occ > 40) barColor = "bg-warning matrix-glow";

                        return (
                            <div key={laneId} className="space-y-2">
                                <div className="flex justify-between items-end text-sm">
                                    <span className="uppercase font-bold tracking-wider opacity-80">Lane {laneId}</span>
                                    <span className="font-lcd tracking-widest">{occ.toFixed(0)}% OCCUPIED</span>
                                </div>

                                {/* Progress Bar Container */}
                                <div className="h-2 w-full bg-lcd-text/10 relative overflow-hidden">
                                    <div
                                        className={`h-full ${barColor} transition-all duration-500`}
                                        style={{ width: `${Math.min(100, occ)}%` }}
                                    />
                                    {/* Scanline effect overlay */}
                                    <div className="absolute inset-0 bg-white/5 w-full h-full animate-[scan_2s_linear_infinite] opacity-30"></div>
                                </div>

                                {/* Queue Metrics */}
                                <div className="flex justify-between items-center text-xs text-lcd-text/60 mt-1">
                                    <div className="flex items-center gap-2">
                                        <span className="uppercase tracking-wide">Queue Length:</span>
                                        <span className={`font-bold font-lcd ${queue > 20 ? 'text-destructive' : 'text-lcd-text'}`}>
                                            {queue.toFixed(1)}m
                                        </span>
                                    </div>
                                    {queue > 30 && (
                                        <span className="text-[10px] bg-destructive/20 text-destructive px-1.5 py-0.5 animate-pulse uppercase">
                                            Congestion
                                        </span>
                                    )}
                                </div>
                            </div>
                        );
                    })
                )}
            </div>
            {children}
        </div>
    );
};

export default LaneAnalysisWidget;
