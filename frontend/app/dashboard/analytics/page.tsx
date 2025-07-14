"use client";

import AuthGuard from "@/components/auth/AuthGuard";
import { UserRole } from "@/lib/auth/roles";
import { Signal, Clock, BatteryFull } from 'lucide-react';
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from "@/components/ui/dropdown-menu";

const AnalyticsPage = () => {
  return (
    <AuthGuard requiredRole={UserRole.PLANNER}>
      <div className="bg-lcd-bg text-lcd-text font-lcd flex flex-col min-h-screen w-full">
        {/* Status Bar */}
        <header className="flex items-center justify-between px-4 py-1 border-b-2 border-lcd-text">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <div className="flex items-center space-x-2 cursor-pointer">
                <Signal size={20} />
                <span className="font-lcd matrix-glow">ANALYTICS</span>
              </div>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" className="matrix-card">
              <DropdownMenuItem asChild>
                <a href="/nodes" className="w-full tracking-normal font-lcd matrix-glow">NODES</a>
              </DropdownMenuItem>
              <DropdownMenuItem asChild>
                <a href="/grid" className="w-full tracking-normal font-lcd matrix-glow">GRID</a>
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <div className="flex items-center space-x-2">
            <Clock size={20} />
            <span className="font-lcd matrix-glow">{new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
            <BatteryFull size={20} />
          </div>
        </header>
        <main className="flex-1 p-4">
          <h1 className="text-2xl font-bold mb-4 text-lcd-text">Analytics Overview</h1>
          <p>This is the analytics page. Content will be added here.</p>
        </main>
      </div>
    </AuthGuard>
  );
};

export default AnalyticsPage;
