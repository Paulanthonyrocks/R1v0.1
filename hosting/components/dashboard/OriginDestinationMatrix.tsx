"use client";

import React, { useEffect, useState, useCallback, useMemo } from 'react';
import { ArrowRight, ArrowRightLeft, Info, MapPin, Activity, MoveRight, ExternalLink } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useAPI } from '@/lib/hooks/useAPI';
import { useAuth } from '@/lib/auth/AuthProvider';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import { motion, AnimatePresence } from 'framer-motion';
import { cn } from '@/lib/utils';

interface ODMatrixProps {
    hours?: number;
}

export const OriginDestinationMatrix: React.FC<ODMatrixProps> = ({ hours = 1 }) => {
    const { token } = useAuth();
    const { feeds } = useRealtimeUpdates();
    const api = useAPI();
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [hoveredCell, setHoveredCell] = useState<{o: string, d: string} | null>(null);

    const fetchData = useCallback(async () => {
        if (!token) return;
        setLoading(true);
        try {
            const response = await api.get<any>(`/api/v1/analytics/od-matrix?hours=${hours}`, undefined, {
                headers: {
                    'Bypass-Tunnel-Reminder': 'true'
                }
            });
            setData(response);
        } catch (e) {
            console.error("OD Matrix fetch failed:", e);
        } finally {
            setLoading(false);
        }
    }, [api, hours, token]);

    useEffect(() => {
        if (token) {
            fetchData();
        }
        
        const interval = setInterval(() => {
            if (token) fetchData();
        }, 30000);
        
        return () => clearInterval(interval);
    }, [token, fetchData]);

    // Helper to get feed name from ID
    const getFeedName = useCallback((id: string) => {
        const feed = feeds.find(f => f.feed_id === id);
        if (feed) return feed.name;
        // Fallback to pretty ID
        return id.split('_').map(word => word.charAt(0).toUpperCase() + word.slice(1)).join(' ');
    }, [feeds]);

    const matrixData = useMemo(() => {
        if (!data || !data.matrix) return null;
        
        const matrix = data.matrix;
        const origins = Object.keys(matrix).sort();
        const destinations = Array.from(new Set(origins.flatMap(o => Object.keys(matrix[o])))).sort();
        
        let maxVal = 0;
        const flows: {origin: string, dest: string, count: number}[] = [];
        
        origins.forEach(o => {
            destinations.forEach(d => {
                const val = matrix[o][d] || 0;
                if (val > maxVal) maxVal = val;
                if (val > 0) {
                    flows.push({ origin: o, dest: d, count: val });
                }
            });
        });

        const topFlows = flows.sort((a, b) => b.count - a.count).slice(0, 5);

        return { matrix, origins, destinations, maxVal, topFlows };
    }, [data]);

    if (loading && !data) {
        return (
            <div className="h-80 flex flex-col items-center justify-center space-y-4">
                <div className="w-12 h-12 border-2 border-lcd-text/20 border-t-lcd-text animate-spin rounded-full"></div>
                <div className="font-lcd text-xs animate-pulse uppercase tracking-[0.2em]">Synchronizing Network Topology...</div>
            </div>
        );
    }

    if (!matrixData) {
        return (
            <div className="h-80 flex items-center justify-center border-2 border-dashed border-lcd-text/10 bg-lcd-text/[0.02]">
                <div className="text-center space-y-2">
                    <Activity className="mx-auto opacity-20" size={32} />
                    <div className="font-lcd text-[10px] opacity-40 uppercase tracking-widest">Insufficient ReID Correlation Data</div>
                </div>
            </div>
        );
    }

    const { matrix, origins, destinations, maxVal, topFlows } = matrixData;

    return (
        <TooltipProvider>
            <div className="flex flex-col xl:flex-row gap-8">
                {/* Main Matrix Grid */}
                <div className="flex-1 min-w-0">
                    <div className="flex items-center justify-between mb-4">
                        <div className="flex items-center gap-2">
                            <div className="h-2 w-2 rounded-full bg-green-500 animate-pulse" />
                            <span className="text-[10px] font-black uppercase tracking-widest opacity-60">Transition Density Matrix</span>
                        </div>
                        <div className="flex items-center gap-3">
                             <div className="flex items-center gap-1">
                                <div className="w-2 h-2 bg-lcd-text/5 border border-lcd-text/20" />
                                <span className="text-[8px] opacity-40 uppercase">Min</span>
                             </div>
                             <div className="flex items-center gap-1">
                                <div className="w-2 h-2 bg-lcd-text shadow-[0_0_5px_var(--lcd-text)]" />
                                <span className="text-[8px] opacity-40 uppercase">Max</span>
                             </div>
                        </div>
                    </div>

                    <div className="relative border-2 border-lcd-text/10 bg-black/20 p-4">
                        <div className="overflow-x-auto custom-scrollbar">
                            <table className="w-full border-spacing-2 border-separate">
                                <thead>
                                    <tr>
                                        <th className="w-24 p-0">
                                            <div className="text-[8px] font-black opacity-30 text-left uppercase pl-2">
                                                ORIGIN ↓ / DEST →
                                            </div>
                                        </th>
                                        {destinations.map(d => (
                                            <th key={d} className="p-0 min-w-[60px]">
                                                <div className="text-[9px] font-black uppercase tracking-tighter text-lcd-text/60 truncate px-2 text-center pb-2 border-b border-lcd-text/10">
                                                    {getFeedName(d)}
                                                </div>
                                            </th>
                                        ))}
                                    </tr>
                                </thead>
                                <tbody>
                                    {origins.map(o => (
                                        <tr key={o}>
                                            <td className="pr-3 py-1">
                                                <div className="text-[9px] font-black uppercase tracking-tighter text-lcd-text/60 text-right truncate border-r border-lcd-text/10 pr-2">
                                                    {getFeedName(o)}
                                                </div>
                                            </td>
                                            {destinations.map(d => {
                                                const value = matrix[o][d] || 0;
                                                const ratio = maxVal > 0 ? value / maxVal : 0;
                                                const isHovered = hoveredCell?.o === o && hoveredCell?.d === d;
                                                
                                                return (
                                                    <td key={d} className="p-0">
                                                        <Tooltip>
                                                            <TooltipTrigger asChild>
                                                                <motion.div 
                                                                    className={cn(
                                                                        "h-10 w-full cursor-crosshair transition-all duration-300 relative group border border-transparent",
                                                                        value > 0 ? "hover:border-lcd-text hover:z-10" : ""
                                                                    )}
                                                                    onMouseEnter={() => setHoveredCell({o, d})}
                                                                    onMouseLeave={() => setHoveredCell(null)}
                                                                    style={{ 
                                                                        backgroundColor: value > 0 
                                                                            ? `rgba(var(--lcd-text-rgb), ${0.05 + ratio * 0.8})` 
                                                                            : 'rgba(255,255,255,0.02)' 
                                                                    }}
                                                                >
                                                                    {value > 0 && (
                                                                        <div className={cn(
                                                                            "absolute inset-0 flex items-center justify-center text-[10px] font-bold transition-colors",
                                                                            ratio > 0.5 ? "text-lcd-bg" : "text-lcd-text"
                                                                        )}>
                                                                            {value}
                                                                        </div>
                                                                    )}
                                                                    {isHovered && value > 0 && (
                                                                        <motion.div 
                                                                            layoutId="cell-glow"
                                                                            className="absolute inset-0 shadow-[0_0_15px_var(--lcd-text)] opacity-40 pointer-events-none" 
                                                                        />
                                                                    )}
                                                                </motion.div>
                                                            </TooltipTrigger>
                                                            <TooltipContent className="bg-lcd-bg border-2 border-lcd-text text-lcd-text font-lcd rounded-none p-3 shadow-2xl">
                                                                <div className="space-y-1">
                                                                    <div className="flex items-center gap-2 text-[10px] border-b border-lcd-text/20 pb-1 mb-1">
                                                                        <span className="opacity-60 uppercase">Route:</span>
                                                                        <span className="font-black">{getFeedName(o)}</span>
                                                                        <MoveRight size={10} className="opacity-40" />
                                                                        <span className="font-black">{getFeedName(d)}</span>
                                                                    </div>
                                                                    <div className="flex justify-between items-end">
                                                                        <span className="text-[14px] font-black">{value} Transitions</span>
                                                                        <span className="text-[8px] opacity-40">({(ratio * 100).toFixed(0)}% DENSITY)</span>
                                                                    </div>
                                                                </div>
                                                            </TooltipContent>
                                                        </Tooltip>
                                                    </td>
                                                );
                                            })}
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>

                {/* Top Flows & Metadata */}
                <div className="w-full xl:w-72 space-y-6">
                    <div>
                        <div className="flex items-center gap-2 mb-4">
                            <ArrowRightLeft size={16} className="text-lcd-text/40" />
                            <span className="text-[10px] font-black uppercase tracking-[0.2em] opacity-80">Primary Corridors</span>
                        </div>
                        <div className="space-y-3">
                            <AnimatePresence mode="popLayout">
                                {topFlows.map((flow, i) => (
                                    <motion.div 
                                        key={`${flow.origin}-${flow.dest}`}
                                        initial={{ opacity: 0, x: 20 }}
                                        animate={{ opacity: 1, x: 0 }}
                                        transition={{ delay: i * 0.1 }}
                                        className="group p-3 border-2 border-lcd-text/10 bg-lcd-text/[0.02] hover:bg-lcd-text/5 hover:border-lcd-text/30 transition-all flex flex-col gap-2"
                                    >
                                        <div className="flex items-center justify-between text-[8px] font-black opacity-40 group-hover:opacity-100 transition-opacity uppercase tracking-widest">
                                            <span>RANK_0{i+1}</span>
                                            <div className="flex items-center gap-1">
                                                <div className="h-1 w-1 rounded-full bg-lcd-text animate-pulse" />
                                                LIVE_FLOW
                                            </div>
                                        </div>
                                        <div className="flex items-center gap-2 justify-between">
                                            <div className="flex-1 min-w-0">
                                                <div className="text-[10px] font-black truncate uppercase tracking-tighter">
                                                    {getFeedName(flow.origin)}
                                                </div>
                                                <div className="flex items-center gap-1 my-0.5">
                                                    <div className="h-px flex-1 bg-lcd-text/20" />
                                                    <MoveRight size={8} className="text-lcd-text/40" />
                                                    <div className="h-px flex-1 bg-lcd-text/20" />
                                                </div>
                                                <div className="text-[10px] font-black truncate uppercase tracking-tighter">
                                                    {getFeedName(flow.dest)}
                                                </div>
                                            </div>
                                            <div className="text-2xl font-black font-lcd tabular-nums pl-4 text-lcd-text">
                                                {flow.count}
                                            </div>
                                        </div>
                                    </motion.div>
                                ))}
                            </AnimatePresence>
                        </div>
                    </div>

                    <div className="grid grid-cols-2 gap-4">
                        <div className="border-2 border-lcd-text/10 p-3 bg-black/40">
                            <div className="text-[8px] font-black opacity-40 uppercase mb-1">Total Tracks</div>
                            <div className="text-xl font-black font-lcd">{data.metadata.total_tracked_vehicles}</div>
                        </div>
                        <div className="border-2 border-lcd-text/10 p-3 bg-black/40">
                            <div className="text-[8px] font-black opacity-40 uppercase mb-1">ReID Syncs</div>
                            <div className="text-xl font-black font-lcd">{data.metadata.total_transitions}</div>
                        </div>
                    </div>
                </div>
            </div>
        </TooltipProvider>
    );
};

