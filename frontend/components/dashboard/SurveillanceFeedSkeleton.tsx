// components/dashboard/SurveillanceFeedSkeleton.tsx (NEW)
import React from 'react';
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

const SurveillanceFeedSkeleton = () => {
    return (
        <Card className={cn("matrix-glow-card overflow-hidden animate-pulse")}>
            {/* Aspect Ratio Placeholder */}
            <div className="aspect-video bg-muted/30"></div>
            <CardContent className="p-2 space-y-1.5"> {/* Added spacing */}
                <div className="h-3 bg-muted/30 rounded w-4/5"></div> {/* Name Skeleton */}
                <div className="h-2.5 bg-muted/30 rounded w-3/5"></div> {/* Node Skeleton */}
            </CardContent>
        </Card>
    );
};

export default SurveillanceFeedSkeleton;