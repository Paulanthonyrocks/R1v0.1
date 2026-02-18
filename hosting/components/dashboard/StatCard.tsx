// components/dashboard/StatCard.tsx
import React from 'react';
import { Card, CardContent } from "@/components/ui/card";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { ArrowUp, ArrowDown } from 'lucide-react';

import { cn } from "@/lib/utils";
import { StatCardProps as OriginalStatCardProps } from '@/lib/types';

interface StatCardProps extends OriginalStatCardProps {
  statusIcon?: React.ElementType;
  statusColor?: string;
}

const StatCard = React.memo(({ title, value, change, changeText, icon: Icon, statusIcon: StatusIconComponent, statusColor }: StatCardProps) => {
    const isPositive = change.startsWith('+');
    
    // If statusColor is not provided, default to theme text that inverts on hover
    const finalStatusColor = statusColor || "text-lcd-text group-hover:text-lcd-bg";

    return (
        <Card className={cn(
            "matrix-glow-card group transition-all duration-300",
            "w-full h-full",
            // Ensure border is visible on hover/active
            "border border-transparent hover:border-lcd-bg" 
        )}>
            <CardContent className="p-5 flex flex-col h-full">
                <div className="flex items-start justify-between space-x-4 mb-4">
                    <div className="space-y-2 flex-1 min-w-0">
                        <p className="text-sm font-bold text-lcd-text/70 group-hover:text-lcd-bg/80 tracking-widest font-lcd uppercase truncate">{title}</p>
                        <h3 className={cn("text-3xl font-bold tracking-wider text-lcd-text group-hover:text-lcd-bg flex items-center font-lcd matrix-glow")}>
                          {StatusIconComponent && <StatusIconComponent className={cn("mr-2 h-6 w-6", finalStatusColor)} />} 
                          <span className="truncate">{value}</span>
                        </h3>
                    </div>
                    {/* Icon Container: Black on default (Green Card), Green on hover (Black Card) */}
                    <div className="p-3 rounded-none bg-lcd-text text-lcd-bg group-hover:bg-lcd-bg group-hover:text-lcd-text transition-colors duration-300 flex-shrink-0">
                        <Icon className="h-6 w-6 font-lcd" />
                    </div>
                </div>
                
                <TooltipProvider delayDuration={300}>
                    <Tooltip>
                        <TooltipTrigger asChild>
                             <div className={cn("text-xs flex items-center mt-auto cursor-default tracking-widest font-lcd text-lcd-text/80 group-hover:text-lcd-bg/80")}>
                                <span className={cn(
                                    "flex items-center mr-2 font-bold",
                                    isPositive ? "text-lcd-text group-hover:text-lcd-bg" : "text-lcd-text group-hover:text-lcd-bg" // You might want red/green here, but sticking to theme for now
                                )}>
                                    {isPositive ? <ArrowUp className="mr-1 h-3 w-3" /> : <ArrowDown className="mr-1 h-3 w-3" />} 
                                    {change}
                                </span>
                                <span className="opacity-60">vs last 5 updates</span>
                            </div>
                        </TooltipTrigger>
                        <TooltipContent side="bottom" align="start" className="font-lcd tracking-widest bg-lcd-text text-lcd-bg border-lcd-bg">
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