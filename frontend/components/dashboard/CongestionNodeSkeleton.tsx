// components/dashboard/CongestionNodeSkeleton.tsx (NEW)
import React from 'react';
import { cn } from "@/lib/utils";

const CongestionNodeSkeleton = () => {
    return (
        <div className="space-y-2 animate-pulse">
            <div className="flex justify-between items-center">
                 <div className="h-4 bg-muted/30 rounded w-3/5"></div> {/* Name Skeleton */}
                 <div className="h-4 bg-muted/30 rounded w-1/5"></div> {/* Value Skeleton */}
            </div>
            <div className="h-1.5 bg-muted/30 rounded w-full"></div> {/* Progress Bar Skeleton */}
        </div>
    );
};

export default CongestionNodeSkeleton;