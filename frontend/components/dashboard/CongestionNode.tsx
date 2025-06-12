// components/dashboard/CongestionNode.tsx
import React from 'react';
import { Progress } from '@/components/ui/progress';
import { cn } from '@/lib/utils';
import { CongestionNodeProps } from '@/lib/types';
import { CheckCircle2, AlertTriangle, XOctagon } from 'lucide-react'; // Import status icons

const CongestionNode = React.memo(({ id, name, value }: CongestionNodeProps) => {
  const indicatorColorClass = "[&>div]:bg-primary"; // Always black indicator
  const textColorClass = 'text-primary'; // Always black

  let StatusIconComponent = null;
  if (value <= 60) {
    StatusIconComponent = CheckCircle2;
  } else if (value <= 80) {
    StatusIconComponent = AlertTriangle;
  } else {
    StatusIconComponent = XOctagon;
  }

  return (
    <div className="group" title={`${name} - ${value}% congested`}>
      <div className="flex justify-between items-center mb-1 text-sm">
        <span className="font-medium text-foreground truncate pr-2 group-hover:text-primary transition-colors duration-150 tracking-normal" > {/* Added tracking-normal */}
          {name}
        </span>
        <span className={cn(
          "font-mono tabular-nums font-semibold tracking-normal flex items-center", /* Added tracking-normal and flex items-center */
          textColorClass
        )}>
          {StatusIconComponent && <StatusIconComponent className="h-4 w-4 mr-1 text-primary" />} {/* Icon Added */}
          {value.toFixed(0)}%
        </span>
      </div>
      <Progress
        value={value}
        className={cn(
          "h-1.5 group-hover:h-2 transition-all duration-200 ease-in-out",
          "bg-card", // Set the track color to card background
          indicatorColorClass,
          'transition-[width] duration-500 ease-out'
        )}
        aria-label={`Congestion level for ${name}: ${value}%`}
      />
    </div>
  );
});

CongestionNode.displayName = 'CongestionNode';
export default CongestionNode;