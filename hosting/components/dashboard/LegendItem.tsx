// components/dashboard/LegendItem.tsx
import React from 'react';
import { cn } from "@/lib/utils";
import { LegendItemProps } from '@/lib/types'; // Import props type

const LegendItem = React.memo(({ color, text }: LegendItemProps) => {
    return (
        <div className="flex items-center space-x-1.5">
            <div className={cn("w-2.5 h-2.5 rounded-full", color)}></div>
            <span className="text-xs text-muted-foreground">{text}</span>
        </div>
    );
});

LegendItem.displayName = 'LegendItem';
export default LegendItem;