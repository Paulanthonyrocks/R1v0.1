// components/dashboard/StatCard.tsx
import React from 'react';
import { Card, CardContent } from "@/components/ui/card";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { ArrowUp, ArrowDown } from 'lucide-react';
import { cn } from "@/lib/utils";
import { StatCardProps } from '@/lib/types';

const StatCard = React.memo(({ title, value, change, changeText, icon: Icon, valueColor = "text-primary", changeColor }: StatCardProps) => {
    const isPositive = change.startsWith('+');
    const defaultChangeColor = isPositive ? "text-green-500" : "text-amber-500"; // Keep status distinct

    return (
        // Relies on parent grid gap, added hover effect
        <Card className={cn(
            "matrix-glow-card",
            "w-full h-full", // Ensure it fills grid cell
            "transition-transform duration-300 hover:scale-[1.03]" // Hover effect
        )}>
            <CardContent className="p-4 flex flex-col h-full">
                <div className="flex items-start justify-between space-x-2 mb-2">
                    <div className="space-y-1.5 flex-1">
                        <p className="text-sm font-medium text-muted-foreground">{title}</p>
                        <h3 className={cn("text-2xl font-semibold matrix-text-glow tracking-wider", valueColor)}>{value}</h3>
                    </div>
                    <div className="p-2 rounded bg-secondary flex-shrink-0">
                        <Icon className={cn("h-5 w-5", valueColor)} />
                    </div>
                </div>
                <TooltipProvider delayDuration={300}>
                    <Tooltip>
                        <TooltipTrigger asChild>
                             <p className={cn("text-xs flex items-center mt-auto cursor-default", changeColor || defaultChangeColor)}>
                                {isPositive ? <ArrowUp className="mr-1 h-3 w-3" /> : <ArrowDown className="mr-1 h-3 w-3" />} {change}
                            </p>
                        </TooltipTrigger>
                        <TooltipContent side="bottom" align="start">
                            <p>{changeText}</p>
                        </TooltipContent>
                    </Tooltip>
                </TooltipProvider>
            </CardContent>
        </Card>
    );
});

StatCard.displayName = 'StatCard';
export default StatCard;