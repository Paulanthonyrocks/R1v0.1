// components/dashboard/StatCard.tsx
import React from 'react';
import { Card, CardContent } from "@/components/ui/card";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { ArrowUp, ArrowDown, Loader2 } from 'lucide-react';

import { cn } from "@/lib/utils";
import { StatCardProps as OriginalStatCardProps } from '@/lib/types';

interface StatCardProps extends OriginalStatCardProps {
  statusIcon?: React.ElementType;
  statusColor?: string;
}

const StatCard = React.memo(({ title, value, change, changeText, icon: Icon, statusIcon: StatusIconComponent, statusColor }: StatCardProps) => {
    const isPositive = change?.startsWith('+');
    const isLoading = value === '--' || value === 'N/A' || value === null;
    
    const finalStatusColor = statusColor || "text-lcd-text group-hover:text-lcd-bg";

    return (
        <Card className={cn(
            "matrix-glow-card group transition-all duration-500 relative overflow-hidden",
            "w-full h-full bg-lcd-text/5 backdrop-blur-md border-lcd-text/20 hover:border-lcd-text/60",
            "hover:shadow-[0_0_20px_rgba(0,255,0,0.1)]"
        )}>
            {/* Tactical Frame Corners */}
            <div className="absolute top-0 left-0 w-2 h-2 border-t-2 border-l-2 border-lcd-text/40 group-hover:border-lcd-text transition-colors" />
            <div className="absolute top-0 right-0 w-2 h-2 border-t-2 border-r-2 border-lcd-text/40 group-hover:border-lcd-text transition-colors" />
            <div className="absolute bottom-0 left-0 w-2 h-2 border-b-2 border-l-2 border-lcd-text/40 group-hover:border-lcd-text transition-colors" />
            <div className="absolute bottom-0 right-0 w-2 h-2 border-b-2 border-r-2 border-lcd-text/40 group-hover:border-lcd-text transition-colors" />

            <CardContent className="p-5 flex flex-col h-full relative z-10">
                <div className="flex items-start justify-between space-x-4 mb-4">
                    <div className="space-y-2 flex-1 min-w-0">
                        <p className="text-[10px] font-bold text-lcd-text/60 group-hover:text-lcd-bg/80 tracking-widest font-lcd uppercase truncate">
                            {title}
                        </p>
                        <h3 className={cn(
                            "text-3xl font-bold tracking-wider text-lcd-text group-hover:text-lcd-bg flex items-center font-lcd matrix-glow transition-all",
                            isLoading && "opacity-50"
                        )}>
                          {StatusIconComponent && <StatusIconComponent className={cn("mr-2 h-6 w-6", finalStatusColor)} />} 
                          <span className="truncate">
                            {isLoading ? (
                                <span className="text-sm font-normal italic opacity-70 animate-pulse">Sensing...</span>
                            ) : value}
                          </span>
                        </h3>
                    </div>
                    <div className="p-2 rounded-none bg-lcd-text text-lcd-bg group-hover:bg-lcd-bg group-hover:text-lcd-text transition-all duration-300 flex-shrink-0 shadow-sm">
                        <Icon className="h-5 w-5 font-lcd" />
                    </div>
                </div>
                
                <TooltipProvider delayDuration={300}>
                    <Tooltip>
                        <TooltipTrigger asChild>
                             <div className={cn("text-[10px] flex items-center mt-auto cursor-default tracking-widest font-lcd text-lcd-text/60 group-hover:text-lcd-bg/60")}>
                                <span className={cn(
                                    "flex items-center mr-2 font-bold",
                                    isPositive ? "text-lcd-text group-hover:text-lcd-bg" : "text-lcd-text group-hover:text-lcd-bg"
                                )}>
                                    {isLoading ? (
                                        <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                                    ) : (
                                        isPositive ? <ArrowUp className="mr-1 h-3 w-3" /> : <ArrowDown className="mr-1 h-3 w-3" />
                                    )} 
                                    {isLoading ? "SYNCHRONIZING" : change}
                                </span>
                                <span className="opacity-40">{isLoading ? "Awaiting Uplink" : "vs last 5 updates"}</span>
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
