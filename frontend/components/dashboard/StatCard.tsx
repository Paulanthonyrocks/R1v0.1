// components/dashboard/StatCard.tsx
import React from 'react';
import { Card, CardContent } from "@/components/ui/card";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { ArrowUp, ArrowDown } from 'lucide-react';

import { cn } from "@/lib/utils";
import { StatCardProps as OriginalStatCardProps } from '@/lib/types'; // Renamed to avoid conflict

// Extend StatCardProps to include an optional statusIcon
interface StatCardProps extends OriginalStatCardProps {
  statusIcon?: React.ElementType;
}

const StatCard = React.memo(({ title, value, change, changeText, icon: Icon, statusIcon: StatusIconComponent }: StatCardProps) => {
    const isPositive = change.startsWith('+');
    // const defaultChangeColor = isPositive ? "text-primary" : "text-warning"; // Original
    const defaultChangeColor = "text-primary"; // Neutralized to black

    return (
        // Relies on parent grid gap, added hover effect
        <Card className={cn(
            "matrix-glow-card group", // Added 'group' class here
            "w-full h-full", // Ensure it fills grid cell
            
        )}>
            <CardContent className="p-4 flex flex-col h-full">
                <div className="flex items-start justify-between space-x-2 mb-2">
                    <div className="space-y-1.5 flex-1">
                        <p className="text-sm font-medium text-lcd-text group-hover:text-lcd-bg tracking-normal font-lcd matrix-glow">{title}</p>
                        <h3 className={cn("text-2xl font-semibold tracking-normal text-lcd-text group-hover:text-lcd-bg flex items-center font-lcd matrix-glow")}>
                          {StatusIconComponent && <StatusIconComponent className="mr-2 h-5 w-5" />} {value}
                        </h3>
                    </div>
                    <div className="p-2 rounded bg-secondary flex-shrink-0">
                        <Icon className={cn("h-5 w-5 text-lcd-text group-hover:text-lcd-bg font-lcd matrix-glow")} />
                    </div>
                </div>
                <TooltipProvider delayDuration={300}>
                    <Tooltip>
                        <TooltipTrigger asChild>
                             <p className={cn("text-xs flex items-center mt-auto cursor-default tracking-normal font-lcd matrix-glow text-lcd-text group-hover:text-lcd-bg")}>
                                {isPositive ? <ArrowUp className="mr-1 h-3 w-3" /> : <ArrowDown className="mr-1 h-3 w-3" />} {change}
                            </p>
                        </TooltipTrigger>
                        <TooltipContent side="bottom" align="start" className="tracking-normal font-lcd matrix-glow"> {/* Added tracking-normal to TooltipContent text */}
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