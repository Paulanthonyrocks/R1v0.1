import React, { useMemo } from 'react';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Leaf, Wind, Activity } from 'lucide-react';
import { cn } from "@/lib/utils";

interface EcoStatsWidgetProps {
    vehicles: any[]; // Tracked vehicles with class_id
    className?: string;
}

const EMISSION_FACTORS: Record<string, number> = {
    'car': 0.12,      // kg CO2 per km
    'motorcycle': 0.08,
    'bus': 0.8,
    'truck': 1.2,
    'unknown': 0.15
};

const EcoStatsWidget: React.FC<EcoStatsWidgetProps> = ({ vehicles, className }) => {
    
    const stats = useMemo(() => {
        let totalCO2Rate = 0; // kg/km for the local fleet
        let vehicleCounts: Record<string, number> = {};

        vehicles.forEach(v => {
            const type = v.class_name || 'unknown';
            const factor = EMISSION_FACTORS[type] || EMISSION_FACTORS['unknown'];
            totalCO2Rate += factor;
            vehicleCounts[type] = (vehicleCounts[type] || 0) + 1;
        });

        // Estimate hourly impact if this flow persists
        const hourlyCO2 = totalCO2Rate * 10; // Simple heuristic: rate * avg speed * density

        return {
            totalCO2Rate,
            hourlyCO2,
            vehicleCounts
        };
    }, [vehicles]);

    return (
        <Card className={cn("matrix-glow-card font-lcd matrix-glow", className)}>
            <CardHeader className="p-3 pb-0">
                <CardTitle className="text-sm flex items-center gap-2 text-primary">
                    <Leaf className="h-4 w-4" />
                    ENVIRONMENTAL IMPACT
                </CardTitle>
            </CardHeader>
            <CardContent className="p-3 pt-2">
                <div className="space-y-3">
                    <div className="flex justify-between items-end border-b border-primary/20 pb-2">
                        <span className="text-[10px] text-muted-foreground uppercase">Est. CO2 Output</span>
                        <div className="text-right">
                            <span className="text-xl font-bold text-primary">
                                {stats.totalCO2Rate.toFixed(2)}
                            </span>
                            <span className="text-[10px] ml-1 opacity-70">KG/KM</span>
                        </div>
                    </div>
                    
                    <div className="grid grid-cols-2 gap-2">
                        <div className="p-2 bg-black/40 border border-primary/10">
                            <div className="flex items-center gap-1 text-[8px] text-muted-foreground mb-1">
                                <Wind className="h-3 w-3" />
                                HOURLY PROJECTION
                            </div>
                            <div className="text-md font-bold text-lcd-text">
                                {stats.hourlyCO2.toFixed(1)} <span className="text-[8px]">KG/H</span>
                            </div>
                        </div>
                        <div className="p-2 bg-black/40 border border-primary/10">
                            <div className="flex items-center gap-1 text-[8px] text-muted-foreground mb-1">
                                <Activity className="h-3 w-3" />
                                AIR QUALITY INDEX
                            </div>
                            <div className="text-md font-bold text-yellow-500">
                                {stats.totalCO2Rate > 2 ? 'FAIR' : 'GOOD'}
                            </div>
                        </div>
                    </div>

                    <div className="pt-1">
                        <div className="text-[8px] text-muted-foreground mb-1 uppercase">Fleet Distribution</div>
                        <div className="flex h-1.5 w-full bg-primary/5 rounded-full overflow-hidden">
                            {Object.entries(stats.vehicleCounts).map(([type, count], i) => (
                                <div 
                                    key={type}
                                    style={{ width: `${(count / vehicles.length) * 100}%` }}
                                    className={cn(
                                        "h-full",
                                        i === 0 ? "bg-primary" : i === 1 ? "bg-primary/60" : "bg-primary/30"
                                    )}
                                    title={`${type}: ${count}`}
                                />
                            ))}
                        </div>
                    </div>
                </div>
            </CardContent>
        </Card>
    );
};

export default EcoStatsWidget;
