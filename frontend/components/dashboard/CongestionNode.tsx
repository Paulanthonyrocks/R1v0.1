// components/dashboard/CongestionNode.tsx
import React from 'react';
import { Progress } from '@/components/ui/progress';
import { cn } from '@/lib/utils';
import { CongestionNodeProps } from '@/lib/types';

const CongestionNode = React.memo(({ id, name, value }: CongestionNodeProps) => {
  // Determine color based on congestion level using theme semantic colors
  const indicatorColorClass = value > 80
    ? "[&>div]:bg-destructive" // Targets ProgressPrimitive.Indicator
    : value > 60
      ? "[&>div]:bg-warning"
      : "[&>div]:bg-primary";

  const textColorClass = value > 80
    ? 'text-destructive'
    : value > 60
      ? 'text-warning'
      : 'text-primary'; // Default to primary color for text

  return (
    <div className="group" title={`${name} - ${value}% congested`}>
      <div className="flex justify-between items-center mb-1 text-sm">
        <span className="font-medium text-foreground truncate pr-2 group-hover:text-primary transition-colors duration-150" >
          {name}
        </span>
        <span className={cn(
          "font-mono tabular-nums font-semibold",
          textColorClass // Use themed text color
        )}>
          {value.toFixed(0)}%
        </span>
      </div>
      <Progress
        value={value}
        className={cn(
          "h-1.5 group-hover:h-2 transition-all duration-200 ease-in-out",
          "bg-card", // Set the track color to card background
          indicatorColorClass, // Apply the class to color the indicator
          'transition-[width] duration-500 ease-out'
        )}
        aria-label={`Congestion level for ${name}: ${value}%`}
      />
    </div>
  );
});

CongestionNode.displayName = 'CongestionNode';
export default CongestionNode;