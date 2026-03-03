"use client";

import React, { useEffect, useState, useCallback } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ArrowRight, ArrowRightLeft, Info } from 'lucide-react';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { useAPI } from '@/lib/hooks/useAPI';
import useAuth from '@/lib/hook/useAuth';

interface ODMatrixProps {
    hours?: number;
}

export const OriginDestinationMatrix: React.FC<ODMatrixProps> = ({ hours = 1 }) => {
    const { token } = useAuth();
    const api = useAPI();
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(true);

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

    if (loading && !data) return <div className="h-64 flex items-center justify-center font-lcd opacity-50">CALCULATING MATRIX...</div>;
    if (!data || !data.matrix) return <div className="h-64 flex items-center justify-center font-lcd opacity-50 border border-dashed border-lcd-text/20">NO CROSS-FEED DATA AVAILABLE</div>;

    const matrix = data.matrix;
    const origins = Object.keys(matrix);
    const destinations = Array.from(new Set(origins.flatMap(o => Object.keys(matrix[o]))));

    return (
        <div className="flex flex-col space-y-4">
            <div className="overflow-x-auto custom-scrollbar">
                <table className="w-full border-collapse font-lcd text-xs">
                    <thead>
                        <tr>
                            <th className="p-2 border border-lcd-text/20 bg-lcd-text/5 text-left">ORIGIN \ DEST</th>
                            {destinations.map(d => (
                                <th key={d} className="p-2 border border-lcd-text/20 bg-lcd-text/5 min-w-[80px]">{d.split('_').pop()}</th>
                            ))}
                        </tr>
                    </thead>
                    <tbody>
                        {origins.map(o => (
                            <tr key={o} className="hover:bg-lcd-text/5">
                                <td className="p-2 border border-lcd-text/20 font-bold bg-lcd-text/5">{o.split('_').pop()}</td>
                                {destinations.map(d => {
                                    const value = matrix[o][d] || 0;
                                    const intensity = Math.min(value * 10, 100);
                                    return (
                                        <td 
                                            key={d} 
                                            className="p-2 border border-lcd-text/20 text-center"
                                            style={{ backgroundColor: value > 0 ? `rgba(var(--lcd-text-rgb), ${intensity/100})` : 'transparent' }}
                                        >
                                            <span className={value > 0 ? 'text-lcd-bg font-bold' : 'opacity-20'}>{value}</span>
                                        </td>
                                    );
                                })}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
            
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-4">
                <div className="matrix-card p-3 flex flex-col items-center">
                    <span className="text-[10px] opacity-60 uppercase">Tracked (Cross-Feed)</span>
                    <span className="text-xl font-bold">{data.metadata.total_tracked_vehicles}</span>
                </div>
                <div className="matrix-card p-3 flex flex-col items-center">
                    <span className="text-[10px] opacity-60 uppercase">Transitions</span>
                    <span className="text-xl font-bold">{data.metadata.total_transitions}</span>
                </div>
            </div>
        </div>
    );
};
