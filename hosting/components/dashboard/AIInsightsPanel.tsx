"use client";

import React, { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BrainCircuit, Sparkles, AlertCircle, TrendingDown, ArrowRight, Activity } from 'lucide-react';
import { cn } from "@/lib/utils";
import { formatFeedName } from '@/lib/formatters';

interface AIInsightsPanelProps {
    metrics: any;
    feedName: string;
    className?: string;
}

const AIInsightsPanel: React.FC<AIInsightsPanelProps> = ({ metrics, feedName, className }) => {
    const [insight, setInsight] = useState<string>("Analyzing real-time patterns...");
    const [priority, setPriority] = useState<'low' | 'medium' | 'high'>('low');

    useEffect(() => {
        const generateMockInsight = () => {
            if (!metrics) return;

            const speed = metrics.average_speed_kmh || 50;
            const count = metrics.total_vehicles || 0;
            const displayFeedName = formatFeedName(feedName);

            if (speed < 20 && count > 10) {
                setInsight(`Congestion spike detected at ${displayFeedName}. Recommend adjusting signal timings at the upstream intersection to flush the queue.`);
                setPriority('high');
            } else if (count > 50) {
                setInsight(`High volume trend observed at ${displayFeedName}. Flow is stable but approaching saturation. No immediate intervention required.`);
                setPriority('medium');
            } else {
                setInsight(`Normal flow patterns maintained at ${displayFeedName}. Current capacity utilization is at ${Math.min(100, (count / 20) * 100).toFixed(0)}%.`);
                setPriority('low');
            }
        };

        const timer = setTimeout(generateMockInsight, 2000);
        return () => clearTimeout(timer);
    }, [metrics, feedName]);
// ... (rest of file)

    const priorityConfig = {
        high: { color: 'text-red-500', border: 'border-red-500', bg: 'bg-red-500/10', label: 'CRITICAL', glow: 'shadow-[0_0_15px_rgba(239,68,68,0.4)]' },
        medium: { color: 'text-yellow-500', border: 'border-yellow-500', bg: 'bg-yellow-500/10', label: 'WARNING', glow: 'shadow-[0_0_15px_rgba(234,179,8,0.4)]' },
        low: { color: 'text-emerald-500', border: 'border-emerald-500', bg: 'bg-emerald-500/10', label: 'NOMINAL', glow: 'shadow-[0_0_15px_rgba(16,185,129,0.4)]' },
    };

    const config = priorityConfig[priority];

    return (
        <Card className={cn(
            "relative overflow-hidden border-lcd-text/30 bg-industrial-panel text-lcd-text font-lcd",
            "shadow-[inset_0_0_20px_rgba(0,0,0,0.5)] transition-all duration-500",
            className
        )}>
            {/* Tactical Corner Accents */}
            <div className="absolute top-0 left-0 w-2 h-2 border-t-2 border-l-2 border-lcd-text/50" />
            <div className="absolute top-0 right-0 w-2 h-2 border-t-2 border-r-2 border-lcd-text/50" />
            <div className="absolute bottom-0 left-0 w-2 h-2 border-b-2 border-l-2 border-lcd-text/50" />
            <div className="absolute bottom-0 right-0 w-2 h-2 border-b-2 border-r-2 border-lcd-text/50" />

            <CardHeader className="p-4 pb-2 relative">
                <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2 text-xs tracking-widest text-lcd-text/70 uppercase">
                        <Activity className="h-3 w-3 animate-pulse" />
                        <span>System Intelligence</span>
                    </div>
                    <div className={cn(
                        "text-[10px] px-2 py-0.5 border font-bold uppercase tracking-tighter animate-pulse",
                        config.color, config.border, config.bg
                    )}>
                        {config.label}
                    </div>
                </div>
                <CardTitle className={cn(
                    "text-lg font-lcd flex items-center gap-2 tracking-tight",
                    config.color
                )}>
                    <BrainCircuit className="h-5 w-5" />
                    NEURAL INSIGHT ENGINE
                    <Sparkles className="h-4 w-4 ml-auto opacity-50" />
                </CardTitle>
            </CardHeader>

            <CardContent className="p-4 pt-0">
                <div className="space-y-4">
                    {/* Insight Box with dynamic glow */}
                    <div className={cn(
                        "relative p-4 border-l-4 bg-black/40 transition-all duration-500",
                        config.border, config.glow
                    )}>
                        <p className="text-sm leading-relaxed font-lcd opacity-90">
                            {insight}
                        </p>
                        <div className="absolute -right-1 -bottom-1 opacity-10">
                            <BrainCircuit className="h-12 w-12" />
                        </div>
                    </div>

                    {/* Tactical Action Area */}
                    <div className="grid grid-cols-1 gap-2">
                        <div className="p-3 border border-lcd-text/20 bg-black/60 flex items-center justify-between group hover:border-lcd-text/50 transition-colors cursor-pointer">
                            <div className="flex items-center gap-3">
                                <div className={cn("p-1.5", config.bg, config.color)}>
                                    <ArrowRight className="h-4 w-4" />
                                </div>
                                <div>
                                    <span className="text-[10px] text-lcd-text/40 block uppercase tracking-tighter">Optimal Recommendation</span>
                                    <span className="text-xs font-lcd uppercase">{priority === 'high' ? 'Deploy Manual Override' : 'Maintain Automatic Control'}</span>
                                </div>
                            </div>
                            <div className="h-2 w-2 rounded-full bg-lcd-text animate-ping" />
                        </div>
                    </div>

                    {/* Footer Metadata */}
                    <div className="flex justify-between items-center text-[9px] text-lcd-text/30 uppercase tracking-widest font-sans">
                        <div className="flex items-center gap-2">
                            <span className="w-1 h-1 rounded-full bg-lcd-text/30" />
                            <span>Gemini Ultra-Lite Reasoning</span>
                        </div>
                        <div className="flex items-center gap-2">
                            <span>Confidence: 94.2%</span>
                            <span className="w-1 h-1 rounded-full bg-lcd-text/30" />
                        </div>
                    </div>
                </div>
            </CardContent>
        </Card>
    );
};

export default AIInsightsPanel;
