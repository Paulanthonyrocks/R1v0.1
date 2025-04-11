// components/dashboard/CongestionNode.tsx
import React from 'react';
import { Progress } from '@/components/ui/progress'; // Assuming path is correct
import { cn } from '@/lib/utils';
import { CongestionNodeProps } from '@/lib/types';

const CongestionNode = React.memo(({ id, name, value }: CongestionNodeProps) => {
  // Determine color based on congestion level
  const progressColor = value > 80
    ? 'bg-red-500' // Destructive color for high congestion
    : value > 60
      ? 'bg-amber-400' // Warning color for medium congestion
      : 'bg-green-500'; // Normal color

  const textColor = value > 80
    ? 'text-red-500'
    : value > 60
      ? 'text-amber-400'
      : 'text-green-500';

  return (
    // Add group for hover effects, title attribute for full name/value
    <div className="group" title={`${name} - ${value}% congested`}>
      <div className="flex justify-between items-center mb-1 text-sm">
        {/* Node Name: Truncate long names, change color on group hover */}
        <span className="font-medium text-foreground truncate pr-2 group-hover:text-primary transition-colors duration-150" >
          {name}
        </span>
        {/* Congestion Value: Use mono font, dynamic text color */}
        <span className={cn(
          "font-mono tabular-nums font-semibold", // Styling for numbers
          textColor
        )}>
          {value.toFixed(0)}% {/* Show integer percentage */}
        </span>
      </div>
      {/* Progress Bar: Animate height on group hover, smooth value transitions */}
      <Progress
        value={value}
        className="h-1.5 group-hover:h-2 transition-all duration-200 ease-in-out" // Height transition
        indicatorClassName={cn(
          progressColor,
          'transition-[width] duration-500 ease-out' // Smooth width change (value change)
        )}
        aria-label={`Congestion level for ${name}: ${value}%`}
      />
    </div>
  );
});

CongestionNode.displayName = 'CongestionNode';
export default CongestionNode;