"use client";

import React from 'react';
import DashboardHeader from './DashboardHeader';
import { cn } from '@/lib/utils';

interface DashboardShellProps {
    children: React.ReactNode;
    className?: string;
}

const DashboardShell: React.FC<DashboardShellProps> = ({ children, className }) => {
    return (
        // The outer div establishes the Dark Theme (Dark Background, Light Text) for the dashboard content area
        <div className="bg-lcd-bg text-lcd-text font-lcd flex flex-col min-h-screen w-full selection:bg-lcd-text selection:text-lcd-bg">
            <DashboardHeader />
            <main className={cn("flex-1 flex flex-col min-h-0 p-4 md:p-6 max-w-[1800px] mx-auto w-full", className)}>
                {children}
            </main>
        </div>
    );
};

export default DashboardShell;
