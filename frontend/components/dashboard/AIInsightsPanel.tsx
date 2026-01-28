import React, { useState, useEffect } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BrainCircuit, Sparkles, AlertCircle, TrendingDown, ArrowRight } from 'lucide-react';
import { cn } from "@/lib/utils";

interface AIInsightsPanelProps {
    metrics: any;
    feedName: string;
    className?: string;
}

const AIInsightsPanel: React.FC<AIInsightsPanelProps> = ({ metrics, feedName, className }) => {
    const [insight, setInsight] = useState<string>("Analyzing real-time patterns...");
    const [priority, setPriority] = useState<'low' | 'medium' | 'high'>('low');

    useEffect(() => {
        // Mocking AI response logic based on metrics
        const generateMockInsight = () => {
            if (!metrics) return;

            const speed = metrics.average_speed_kmh || 50;
            const count = metrics.total_vehicles || 0;

            if (speed < 20 && count > 10) {
                setInsight(`Congestion spike detected at ${feedName}. Recommend adjusting signal timings at the upstream intersection to flush the queue.`);
                setPriority('high');
            } else if (count > 50) {
                setInsight(`High volume trend observed. Flow is stable but approaching saturation. No immediate intervention required.`);
                setPriority('medium');
            } else {
                setInsight(`Normal flow patterns maintained. Current capacity utilization is at ${Math.min(100, (count / 20) * 100).toFixed(0)}%.`);
                setPriority('low');
            }
        };

        const timer = setTimeout(generateMockInsight, 2000);
        return () => clearTimeout(timer);
    }, [metrics, feedName]);

    return (
        <Card className={cn("matrix-glow-card border-primary/30 bg-black/60", className)}>
            <CardHeader className="p-4 pb-2">
                <CardTitle className="text-sm font-lcd flex items-center gap-2 text-primary matrix-glow">
                    <BrainCircuit className="h-4 w-4 animate-pulse" />
                    NEURAL INSIGHT ENGINE
                    <Sparkles className="h-3 w-3 text-yellow-500 ml-auto" />
                </CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
                <div className="space-y-4">
                    <div className={cn(
                        "p-3 rounded-none border-l-2 bg-primary/5 font-lcd",
                        priority === 'high' ? "border-red-500 text-red-100" :
                            priority === 'medium' ? "border-yellow-500 text-yellow-100" :
                                "border-primary text-primary-foreground"
                    )}>
                        <p className="text-xs leading-relaxed">
                            {insight}
                        </p>
                    </div>

                    <div className="flex gap-2">
                        <div className="flex-1 p-2 border border-primary/20 bg-black/40">
                            <span className="text-[10px] text-muted-foreground block mb-1">RECOMMENDATION</span>
                            <div className="flex items-center gap-2 text-[10px] text-lcd-text">
                                <ArrowRight className="h-3 w-3 text-primary" />
                                {priority === 'high' ? 'Deploy Manual Override' : 'Maintain Automatic Control'}
                            </div>
                        </div>
                    </div>

                    <div className="flex justify-between items-center text-[8px] text-muted-foreground uppercase opacity-50">
                        <span>Gemini Ultra-Lite Reasoning</span>
                        <span>Confidence: 94.2%</span>
                    </div>
                </div>
            </CardContent>
        </Card>
    );
};

export default AIInsightsPanel;
