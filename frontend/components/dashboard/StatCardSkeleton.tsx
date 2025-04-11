// components/dashboard/StatCardSkeleton.tsx
import React from 'react';
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const StatCardSkeleton = () => {
    return (
        <Card className={cn(
            "matrix-glow-card", // Use same base style
            "w-full h-full",
            "animate-pulse" // Add pulse animation
        )}>
            <CardContent className="p-4 flex flex-col h-full">
                <div className="flex items-start justify-between space-x-2 mb-2">
                    <div className="space-y-2.5 flex-1"> {/* Adjusted spacing */}
                        <div className="h-4 bg-muted/30 rounded w-3/5"></div> {/* Skeleton for title */}
                        <div className="h-6 bg-muted/30 rounded w-2/5"></div> {/* Skeleton for value */}
                    </div>
                    <div className="p-2 rounded bg-muted/30 flex-shrink-0">
                        <div className="h-5 w-5 bg-muted/10 rounded"></div> {/* Skeleton for icon */}
                    </div>
                </div>
                <div className="h-3 bg-muted/30 rounded w-1/3 mt-auto"></div> {/* Skeleton for change text */}
            </CardContent>
        </Card>
    );
};

export default StatCardSkeleton;