"use client";

import React, { useState, useEffect } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Signal, BatteryFull, Map as MapIcon, BarChart3, LayoutGrid, Home, AlertTriangle, Camera, Navigation, TrendingUp, Terminal, Zap, CloudSun, ShieldCheck, Cpu, Activity, Radio } from 'lucide-react';
import { DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem } from "@/components/ui/dropdown-menu";
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import { cn } from '@/lib/utils';

const DashboardHeader: React.FC = () => {
    const pathname = usePathname();
    const { isConnected } = useRealtimeUpdates();
    const [time, setTime] = useState("");
    const [coords, setCoords] = useState({ x: "00.00", y: "00.00" });

    useEffect(() => {
        const updateTime = () => setTime(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }));
        updateTime();
        const interval = setInterval(updateTime, 1000);
        
        const coordInterval = setInterval(() => {
            setCoords({
                x: (Math.random() * 100).toFixed(2).padStart(5, '0'),
                y: (Math.random() * 100).toFixed(2).padStart(5, '0')
            });
        }, 3000);

        return () => {
            clearInterval(interval);
            clearInterval(coordInterval);
        };
    }, []);

    const allNavItems = [
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

    // Only show these in the main bar to reduce clutter
    const primaryNavItems = allNavItems.filter(item => 
        ['/dashboard', '/dashboard/map', '/anomalies', '/logs'].includes(item.href)
    );

    return (
        <header className="flex flex-col w-full sticky top-0 z-[100] font-lcd select-none bg-industrial-bg/30 backdrop-blur-3xl">
            {/* TOP BAR: System Health Ticker */}
            <div className="bg-lcd-green text-industrial-bg px-6 py-2 flex items-center justify-between text-[9px] font-bold uppercase tracking-widest border-b border-industrial-bg">
                <div className="flex items-center gap-6 overflow-hidden">
                    <div className="flex items-center gap-2 whitespace-nowrap">
                        <ShieldCheck size={10} className="animate-pulse" />
                        <span>SECURE_LINK: <span className="text-industrial-bg/80">ACTIVE</span></span>
                    </div>
                    <div className="flex items-center gap-2 whitespace-nowrap">
                        <Cpu size={10} />
                        <span>CORE_SYS: <span className="text-industrial-bg/80">NOMINAL</span></span>
                    </div>
                    <div className="hidden md:flex items-center gap-2 whitespace-nowrap opacity-70">
                        <Radio size={10} />
                        <span>SYNC_MODE: <span className="text-industrial-bg/80">QUANTUM_LOCK</span></span>
                    </div>
                    <div className="hidden lg:flex items-center gap-2 whitespace-nowrap opacity-70">
                        <Activity size={10} />
                        <span>LATENCY: <span className="text-industrial-bg/80">12ms</span></span>
                    </div>
                </div>
                <div className="flex items-center gap-4 whitespace-nowrap">
                    <span className="opacity-80">REGION: NORTH-SECTOR_01</span>
                    <span className="bg-industrial-bg text-lcd-green px-1 animate-pulse">● LIVE</span>
                </div>
            </div>

            {/* MAIN HEADER BAR */}
            <div className="text-lcd-green flex items-center justify-between px-4 py-3 border-b border-lcd-green/40 relative font-mono">
                
                {/* LOGO SECTION: Refined Tactical Module */}
                <div className="flex items-center gap-4 z-10">
                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <div 
                                id="mobile-menu-trigger"
                                className="group relative flex items-center gap-3 px-4 py-2 border border-lcd-green/40 bg-lcd-green/5 hover:bg-lcd-green hover:text-industrial-bg transition-all duration-200 cursor-pointer"
                            >
                                {/* Tactical Corner Brackets */}
                                <div className="absolute -top-px -left-px w-1.5 h-1.5 border-t-2 border-l-2 border-lcd-green group-hover:border-industrial-bg" />
                                <div className="absolute -top-px -right-px w-1.5 h-1.5 border-t-2 border-r-2 border-lcd-green group-hover:border-industrial-bg" />
                                <div className="absolute -bottom-px -left-px w-1.5 h-1.5 border-b-2 border-l-2 border-lcd-green group-hover:border-industrial-bg" />
                                <div className="absolute -bottom-px -right-px w-1.5 h-1.5 border-b-2 border-r-2 border-lcd-green group-hover:border-industrial-bg" />
                                
                                <div className="relative flex items-center gap-3">
                                    <Signal size={18} className="group-hover:animate-pulse" />
                                    <div className="flex flex-col leading-none">
                                        <span className="text-sm font-black tracking-tighter uppercase">Traffic Hub</span>
                                        <span className="text-[8px] opacity-50 uppercase tracking-widest font-bold">S-SYNC v1.0.4</span>
                                    </div>
                                </div>
                            </div>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="start" className="matrix-card min-w-[220px] mt-2 border-lcd-green bg-industrial-panel text-lcd-green">
                            {allNavItems.map((item) => (
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
                
                {/* NAVIGATION: Simplified and Spaced */}
                <nav className="hidden xl:flex items-center h-full gap-2 z-10">
                    {primaryNavItems.map((item) => (
                        <Link 
                            key={item.href} 
                            href={item.href} 
                            className={cn(
                                "flex items-center gap-2 px-4 py-1 text-[10px] font-bold tracking-widest uppercase transition-all relative group",
                                pathname === item.href 
                                    ? "bg-lcd-green text-industrial-bg" 
                                    : "text-lcd-green/50 hover:text-lcd-green hover:bg-lcd-green/10"
                            )}
                        >
                            <item.icon size={12} className={cn("transition-transform group-hover:scale-110", pathname === item.href && "text-industrial-bg")} /> 
                            <span>{item.label}</span>
                        </Link>
                    ))}
                </nav>

                {/* STATUS POD: Cleaned Frame */}
                <div className="flex items-center gap-4 z-10">
                    <div className="relative group hidden lg:flex items-center gap-6 px-4 py-1.5 border border-lcd-green/30 bg-black/40 font-mono text-[9px]">
                        <div className="absolute -top-px -left-px w-1.5 h-1.5 border-t-2 border-l-2 border-lcd-green/30" />
                        <div className="absolute -top-px -right-px w-1.5 h-1.5 border-t-2 border-r-2 border-lcd-green/30" />
                        <div className="absolute -bottom-px -left-px w-1.5 h-1.5 border-b-2 border-l-2 border-lcd-green/30" />
                        <div className="absolute -bottom-px -right-px w-1.5 h-1.5 border-b-2 border-r-2 border-lcd-green/30" />

                        <div className="flex items-center gap-2" title={isConnected ? "WebSocket Connected" : "WebSocket Disconnected"}>
                            <div className={cn("w-1.5 h-1.5", isConnected ? "bg-emerald-500 animate-pulse shadow-[0_0_5px_rgba(16,185,129,1)]" : "bg-red-500 shadow-[0_0_5px_rgba(239,68,68,1)]")}></div>
                            <span className="font-bold uppercase tracking-tighter opacity-80">Uplink: {isConnected ? 'OK' : 'Offline'}</span>
                        </div>
                        <div className="h-3 w-px bg-lcd-green/30" />
                        <div className="flex items-center gap-4 opacity-60">
                            <div className="flex flex-col leading-none">
                                <span className="text-[7px] opacity-50 uppercase">Coord</span>
                                <span className="font-bold">{coords.x}, {coords.y}</span>
                            </div>
                            <div className="flex flex-col leading-none text-right">
                                <span className="text-[7px] opacity-50 uppercase">Epoch</span>
                                <span className="font-bold text-lcd-green">{time}</span>
                            </div>
                        </div>
                    </div>
                    <div className="flex items-center justify-center w-8 h-8 border border-lcd-green/40 bg-lcd-green/5 hover:bg-lcd-green hover:text-industrial-bg transition-all cursor-pointer group">
                        <BatteryFull size={14} className="opacity-70 group-hover:opacity-100" />
                    </div>
                </div>
            </div>
        </header>
    );
};

export default DashboardHeader;