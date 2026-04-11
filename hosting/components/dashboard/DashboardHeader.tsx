"use client";

import React from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Signal, BatteryFull, Map as MapIcon, BarChart3, LayoutGrid, Home, AlertTriangle, Camera, Navigation, TrendingUp, Terminal, Zap, CloudSun, ShieldCheck, Cpu } from 'lucide-react';
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import { cn } from '@/lib/utils';

const DashboardHeader: React.FC = () => {
    const pathname = usePathname();
    const { isConnected } = useRealtimeUpdates();
    const [time, setTime] = React.useState<string>("");

    React.useEffect(() => {
        const updateTime = () => setTime(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }));
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
        <header className="flex flex-col w-full sticky top-0 z-50 font-lcd">
            {/* System Metadata Bar (The "Thin Strip") */}
            <div className="bg-lcd-text text-industrial-bg px-3 py-0.5 flex items-center justify-between text-[9px] font-bold uppercase tracking-widest">
                <div className="flex items-center gap-4">
                    <div className="flex items-center gap-1">
                        <ShieldCheck size={10} />
                        <span>SECURE_LINK: ACTIVE</span>
                    </div>
                    <div className="flex items-center gap-1">
                        <Cpu size={10} />
                        <span>CORE_SYS: NOMINAL</span>
                    </div>
                    <span className="opacity-60">AUTH_LEVEL: 4</span>
                </div>
                <div className="flex items-center gap-4">
                    <span>REGION: NORTH-SECTOR_01</span>
                    <span className="animate-pulse">● LIVE_STREAMING</span>
                </div>
            </div>

            {/* Main Header Bar */}
            <div className="bg-industrial-bg text-lcd-green flex items-center justify-between px-4 py-2 border-b-2 border-lcd-green/30 shadow-[0_4px_20px_rgba(0,0,0,0.5)]">
                {/* Logo Area */}
                <div className="flex items-center gap-4">
                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <div 
                                id="mobile-menu-trigger"
                                className="group relative flex items-center space-x-3 cursor-pointer px-4 py-1.5 border border-lcd-green/50 bg-lcd-green/5 hover:bg-lcd-green hover:text-industrial-bg transition-all duration-200"
                                suppressHydrationWarning
                            >
                                {/* Tactical Corner Brackets for Logo */}
                                <div className="absolute top-0 left-0 w-1 h-1 border-t border-l border-lcd-green" />
                                <div className="absolute top-0 right-0 w-1 h-1 border-t border-r border-lcd-green" />
                                <div className="absolute bottom-0 left-0 w-1 h-1 border-b border-l border-lcd-green" />
                                <div className="absolute bottom-0 right-0 w-1 h-1 border-b border-r border-lcd-green" />
                                
                                <Signal size={20} className="group-hover:animate-pulse" />
                                <div className="flex flex-col leading-none">
                                    <span className="text-lg font-bold tracking-tighter uppercase">TRAFFIC HUB</span>
                                    <span className="text-[8px] opacity-60 uppercase tracking-widest">Neural-Sync v1.0.4</span>
                                </div>
                            </div>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="start" className="matrix-card min-w-[200px] mt-2 border-lcd-green/50 bg-industrial-panel text-lcd-green">
                            {navItems.map((item) => (
                                <DropdownMenuItem key={item.href} asChild>
                                    <Link href={item.href} className={cn("w-full tracking-wider font-lcd py-2 px-4 uppercase text-sm transition-colors", pathname === item.href && "bg-lcd-green text-industrial-bg")}>
                                        {item.label}
                                    </Link>
                                </DropdownMenuItem>
                            ))}
                            <div className="h-px bg-lcd-green/20 my-1" />
                            <DropdownMenuItem asChild>
                                <Link href="/preferences" className="w-full tracking-wider font-lcd py-2 px-4 uppercase text-sm">PREFERENCES</Link>
                            </DropdownMenuItem>
                        </DropdownMenuContent>
                    </DropdownMenu>
                </div>
                
                {/* Desktop Navigation - "Industrial Tab" Style */}
                <nav className="hidden xl:flex items-center h-full">
                    {navItems.map((item) => (
                        <Link 
                            key={item.href} 
                            href={item.href} 
                            className={cn(
                                "flex items-center gap-2 px-3 py-1.5 transition-all uppercase tracking-tighter text-[11px] font-bold border-t-2",
                                pathname === item.href 
                                    ? "bg-lcd-green text-industrial-bg border-lcd-green shadow-[0_0_10px_rgba(182,255,176,0.3)]" 
                                    : "text-lcd-green/60 border-transparent hover:text-lcd-green hover:bg-lcd-green/10"
                            )}
                        >
                            <item.icon size={13} /> 
                            <span>{item.label}</span>
                        </Link>
                    ))}
                </nav>

                {/* Status Indicators Pod */}
                <div className="flex items-center gap-4">
                    <div className="hidden lg:flex items-center gap-4 px-3 py-1 bg-black/60 border border-lcd-green/20 shadow-[inset_0_0_10px_rgba(0,0,0,0.5)]">
                        <div className="flex items-center gap-2" title={isConnected ? "WebSocket Connected" : "WebSocket Disconnected"}>
                            <div className={cn("w-2 h-2 rounded-full", isConnected ? "bg-emerald-500 animate-pulse shadow-[0_0_8px_rgba(16,185,129,0.8)]" : "bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.8)]")}></div>
                            <span className="text-[10px] font-bold uppercase tracking-tighter opacity-80">Uplink: {isConnected ? 'Stable' : 'Offline'}</span>
                        </div>
                        <div className="h-3 w-px bg-lcd-green/20" />
                        <div className="flex items-center gap-2">
                            <span className="text-[10px] opacity-40 uppercase">Epoch</span>
                            <span className="font-lcd text-sm font-bold tabular-nums text-lcd-green">{time}</span>
                        </div>
                    </div>
                    <div className="flex items-center justify-center w-8 h-8 border border-lcd-green/30 bg-lcd-green/5">
                        <BatteryFull size={16} className="opacity-80" />
                    </div>
                </div>
            </div>
        </header>
    );
};

export default DashboardHeader;
