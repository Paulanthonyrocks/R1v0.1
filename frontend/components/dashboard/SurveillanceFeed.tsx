// components/dashboard/SurveillanceFeed.tsx
import React from 'react';
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Eye } from 'lucide-react';
import { cn } from "@/lib/utils";
import { SurveillanceFeedProps } from '@/lib/types'; // Import props type

const SurveillanceFeed = React.memo(({ name, node }: SurveillanceFeedProps) => {
    // Add hover effect to the card
    return (
        <Card className={cn(
            "matrix-glow-card overflow-hidden",
            "transition-transform duration-300 hover:scale-[1.03]" // Hover effect
        )}>
            <div className="bg-black aspect-video flex items-center justify-center relative group cursor-pointer">
                {/* Placeholder for video feed or image */}
                <div className="absolute inset-0 flex items-center justify-center opacity-30 group-hover:opacity-50 transition-opacity duration-300">
                    <Eye className="text-matrix-dark text-4xl" />
                </div>
                {/* Live badge */}
                <Badge
                    variant="default"
                    className="absolute bottom-1.5 right-1.5 bg-primary text-primary-foreground text-[10px] h-4 px-1.5 animate-matrix-pulse"
                >
                    LIVE
                </Badge>
            </div>
            <CardContent className="p-2">
                <h4 className="font-medium text-xs truncate text-foreground group-hover:text-matrix-light transition-colors">{name}</h4>
                <p className="text-[10px] text-muted-foreground">{node}</p>
            </CardContent>
        </Card>
    );
});

SurveillanceFeed.displayName = 'SurveillanceFeed';
export default SurveillanceFeed;