// components/dashboard/PageLayout.tsx
import React from 'react';
import { cn } from "@/lib/utils";
import { PageLayoutProps } from '@/lib/types'; // Ensure type import

const PageLayout = ({ title, children, className }: PageLayoutProps) => {
    return (
        // Section container with vertical spacing only if title exists
        <section className={cn(title ? "flex flex-col gap-3 md:gap-4" : "")}>
            {title && (
                <h2 className="text-lg md:text-xl font-semibold matrix-text-glow tracking-wide mb-2">
                    {title}
                </h2>
            )}
            {/* Grid container with default gaps/cols, allows overrides via className */}
            <div className={cn(
                "grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-6 md:gap-8 place-items-start", // Default grid with standard gap
                className // Apply overrides passed in props (e.g., different cols)
            )}>
                {children}
            </div>
        </section>
    );
};

// Wrap with memo for performance optimization
const MemoizedPageLayout = React.memo(PageLayout);
MemoizedPageLayout.displayName = 'PageLayout';

export default MemoizedPageLayout;