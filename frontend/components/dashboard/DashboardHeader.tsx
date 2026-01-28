"use client";

import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Signal, BatteryFull, Map as MapIcon, BarChart3, LayoutGrid, Home, AlertTriangle, Camera, Navigation, TrendingUp, Terminal, Zap, CloudSun } from 'lucide-react';
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
        { href: '/signals', label: 'SIGNALS', icon: Signal },
        { href: '/impacts', label: 'IMPACTS', icon: CloudSun },
        { href: '/dashboard/tracking', label: 'TRACKING', icon: Navigation },
        { href: '/dashboard/predictive', label: 'FORECASTS', icon: TrendingUp },
        { href: '/logs', label: 'LOGS', icon: Terminal },
    ];

    return (
        <header className="bg-lcd-bg text-lcd-text font-lcd flex items-center justify-between px-4 py-2 border-b-4 border-lcd-text sticky top-0 z-50 shadow-[0_2px_10px_rgba(0,0,0,0.1)]">
            {/* Logo / Mobile Menu */}
            <div className="flex items-center gap-4">
                <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                        <div 
                            id="mobile-menu-trigger"
                            className="flex items-center space-x-3 cursor-pointer group px-3 py-1 border-2 border-lcd-text bg-lcd-text/5 hover:bg-lcd-text hover:text-lcd-bg transition-all"
                            suppressHydrationWarning
                        >
                            <Signal size={22} className="group-hover:animate-pulse" />
                            <span className="font-lcd text-xl font-bold tracking-tighter uppercase">TRAFFIC HUB <span className="text-[10px] opacity-50 ml-1">v1.0</span></span>
                        </div>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="start" className="matrix-card min-w-[200px] mt-2">
                        {navItems.map((item) => (
                            <DropdownMenuItem key={item.href} asChild>
                                <Link href={item.href} className={cn("w-full tracking-wider font-lcd py-2 px-4 uppercase text-sm", pathname === item.href && "bg-lcd-text text-lcd-bg")}>
                                    {item.label}
                                </Link>
                            </DropdownMenuItem>
                        ))}
                        <div className="h-px bg-lcd-text/20 my-1" />
                        <DropdownMenuItem asChild>
                            <Link href="/preferences" className="w-full tracking-wider font-lcd py-2 px-4 uppercase text-sm">PREFERENCES</Link>
                        </DropdownMenuItem>
                    </DropdownMenuContent>
                </DropdownMenu>
            </div>
            
            {/* Desktop Navigation */}
            <nav className="hidden xl:flex items-center space-x-1">
                {navItems.map((item) => (
                    <Link 
                        key={item.href} 
                        href={item.href} 
                        className={cn(
                            "flex items-center gap-2 px-3 py-1.5 transition-all uppercase tracking-tighter text-xs font-bold",
                            pathname === item.href 
                                ? "bg-lcd-text text-lcd-bg" 
                                : "opacity-70 hover:opacity-100 hover:bg-lcd-text/10"
                        )}
                    >
                        <item.icon size={14} /> 
                        <span>{item.label}</span>
                    </Link>
                ))}
            </nav>

            {/* Status Indicators */}
            <div className="flex items-center space-x-6">
                <div className="hidden lg:flex items-center gap-3 px-3 py-1 bg-black/5 border border-lcd-text/10 shadow-inner">
                    <div className="flex items-center gap-2" title={isConnected ? "WebSocket Connected" : "WebSocket Disconnected"}>
                        <div className={cn("w-2 h-2 rounded-full", isConnected ? "bg-green-600 animate-pulse" : "bg-red-600")}></div>
                        <span className="text-[9px] font-bold uppercase opacity-80">Uplink: {isConnected ? 'Stable' : 'Offline'}</span>
                    </div>
                    <div className="h-3 w-px bg-lcd-text/20" />
                    <span className="font-lcd text-xs font-bold tabular-nums">{time}</span>
                </div>
                <div className="flex items-center gap-2">
                    <BatteryFull size={22} className="opacity-80" />
                </div>
            </div>
        </header>
    );
};

export default DashboardHeader;
