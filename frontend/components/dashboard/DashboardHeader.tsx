"use client";

import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Signal, BatteryFull, Map as MapIcon, BarChart3, LayoutGrid, Home, AlertTriangle, Camera, Navigation, TrendingUp, Terminal, Zap } from 'lucide-react';
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import { cn } from '@/lib/utils';

const DashboardHeader: React.FC = () => {
    const pathname = usePathname();
    const { isConnected } = useRealtimeUpdates();
    const [time, setTime] = React.useState<string>("");

    React.useEffect(() => {
        const updateTime = () => setTime(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
        updateTime();
        const interval = setInterval(updateTime, 1000);
        return () => clearInterval(interval);
    }, []);

    const navItems = [
        { href: '/dashboard', label: 'DASHBOARD', icon: Home },
        { href: '/dashboard/map', label: 'MAP', icon: MapIcon },
        { href: '/dashboard/analytics', label: 'ANALYTICS', icon: BarChart3 },
        { href: '/surveillance', label: 'FEEDS', icon: LayoutGrid },
        { href: '/anomalies', label: 'ALERTS', icon: Zap },
        { href: '/incidents', label: 'INCIDENTS', icon: AlertTriangle },
        { href: '/dashboard/tracking', label: 'TRACKING', icon: Navigation },
        { href: '/dashboard/predictive', label: 'FORECASTS', icon: TrendingUp },
        { href: '/logs', label: 'LOGS', icon: Terminal },
    ];

    return (
        <header className="bg-lcd-bg text-lcd-text font-lcd flex items-center justify-between px-4 py-1 border-b-2 border-lcd-text sticky top-0 z-50">
            {/* Logo / Mobile Menu */}
            <DropdownMenu>
                <DropdownMenuTrigger asChild>
                    <div 
                        id="mobile-menu-trigger"
                        className="flex items-center space-x-2 cursor-pointer hover:opacity-80 transition-opacity"
                        suppressHydrationWarning
                    >
                        <Signal size={20} />
                        <span className="font-lcd matrix-glow text-lg font-bold">TRAFFIC HUB</span>
                    </div>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="matrix-card">
                    {navItems.map((item) => (
                        <DropdownMenuItem key={item.href} asChild>
                            <Link href={item.href} className={cn("w-full tracking-normal font-lcd matrix-glow", pathname === item.href && "bg-lcd-text text-lcd-bg")}>
                                {item.label}
                            </Link>
                        </DropdownMenuItem>
                    ))}
                    <DropdownMenuItem asChild>
                        <Link href="/preferences" className="w-full tracking-normal font-lcd matrix-glow">PREFERENCES</Link>
                    </DropdownMenuItem>
                </DropdownMenuContent>
            </DropdownMenu>
            
            {/* Desktop Navigation */}
            <nav className="hidden md:flex items-center space-x-6">
                {navItems.map((item) => (
                    <Link 
                        key={item.href} 
                        href={item.href} 
                        className={cn(
                            "flex items-center gap-1 transition-colors hover:text-lcd-text",
                            pathname === item.href ? "text-lcd-text font-bold underline decoration-2 underline-offset-4" : "opacity-60 hover:opacity-100"
                        )}
                    >
                        <item.icon size={16} /> 
                        <span className="text-xs font-bold tracking-wide">{item.label}</span>
                    </Link>
                ))}
            </nav>

            {/* Status Indicators */}
            <div className="flex items-center space-x-4">
                <div className="flex items-center gap-2" title={isConnected ? "WebSocket Connected" : "WebSocket Disconnected"}>
                    <div className={cn("w-2 h-2 rounded-full", isConnected ? "bg-green-500 animate-pulse" : "bg-red-500")}></div>
                    <span className="text-[10px] opacity-70 hidden sm:inline">WS {isConnected ? 'LIVE' : 'OFFLINE'}</span>
                </div>
                <span className="font-lcd matrix-glow text-sm min-w-[60px] text-right">{time}</span>
                <BatteryFull size={20} />
            </div>
        </header>
    );
};

export default DashboardHeader;
