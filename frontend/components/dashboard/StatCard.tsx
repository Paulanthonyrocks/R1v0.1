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
            "matrix-glow-card",
            "w-full h-full", // Ensure it fills grid cell
            "transition-transform duration-300 hover:scale-[1.03]" // Hover effect
        )}>
            <CardContent className="p-4 flex flex-col h-full">
                <div className="flex items-start justify-between space-x-2 mb-2">
                    <div className="space-y-1.5 flex-1">
                        <p className="text-sm font-medium text-muted-foreground tracking-normal">{title}</p> {/* Added tracking-normal */}
                        {/* Removed matrix-text-glow, valueColor; changed tracking-wider to tracking-normal; added text-primary */}
                        <h3 className={cn("text-2xl font-semibold tracking-normal text-primary flex items-center")}>
                          {StatusIconComponent && <StatusIconComponent className="mr-2 h-5 w-5" />} {/* Render status icon if provided */}
                          {value}
                        </h3>
                    </div>
                    <div className="p-2 rounded bg-secondary flex-shrink-0">
                        {/* Removed valueColor, added text-primary-foreground */}
                        <Icon className={cn("h-5 w-5 text-primary-foreground")} />
                    </div>
                </div>
                <TooltipProvider delayDuration={300}>
                    <Tooltip>
                        <TooltipTrigger asChild>
                             {/* Removed changeColor prop usage, defaultChangeColor is now always text-primary */}
                             <p className={cn("text-xs flex items-center mt-auto cursor-default tracking-normal", defaultChangeColor)}> {/* Added tracking-normal */}
                                {isPositive ? <ArrowUp className="mr-1 h-3 w-3" /> : <ArrowDown className="mr-1 h-3 w-3" />} {change}
                            </p>
                        </TooltipTrigger>
                        <TooltipContent side="bottom" align="start" className="tracking-normal"> {/* Added tracking-normal to TooltipContent text */}
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