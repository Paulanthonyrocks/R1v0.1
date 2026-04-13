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
        <div className="bg-lcd-bg text-lcd-text font-lcd flex flex-col min-h-screen w-full selection:bg-lcd-text selection:text-lcd-bg">
            <DashboardHeader />
            <main className={cn(
                "relative flex-1 flex flex-col min-h-0 p-4 md:p-6 max-w-[1800px] mx-auto w-full", 
                className
            )}>
                {/* Global Tactical Framing */}
                <div className="fixed top-20 left-4 w-4 h-4 border-t-2 border-l-2 border-lcd-text/20 pointer-events-none" />
                <div className="fixed top-20 right-4 w-4 h-4 border-t-2 border-r-2 border-lcd-text/20 pointer-events-none" />
                <div className="fixed bottom-4 left-4 w-4 h-4 border-b-2 border-l-2 border-lcd-text/20 pointer-events-none" />
                <div className="fixed bottom-4 right-4 w-4 h-4 border-b-2 border-r-2 border-lcd-text/20 pointer-events-none" />
                
                {children}
            </main>
        </div>
    );
};

export default DashboardShell;
