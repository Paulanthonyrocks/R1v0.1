"use client";

import React, { useEffect, useState } from 'react';
import { useWebSocket } from '@/lib/websocket/WebSocketProvider';
import { WebSocketMessageType } from '@/lib/websocket/WebSocketClient';
import { Activity, Cpu, Database, HardDrive, Server } from 'lucide-react';
import { Progress } from '@/components/ui/progress';
import { cn } from '@/lib/utils';

interface HealthStats {
    system: {
        cpu_percent: number;
        memory_percent: number;
        memory_used_gb: number;
        disk_percent: number;
    };
    application: {
        active_feeds: number;
        total_feeds: number;
        websocket_clients: number;
    };
    timestamp: string;
}

export const SystemHealthMonitor: React.FC = () => {
    const [stats, setStats] = useState<HealthStats | null>(null);
    const client = useWebSocket();

    useEffect(() => {
        const unsubscribe = client.subscribe(WebSocketMessageType.GENERAL_NOTIFICATION, (data: any) => {
            if (data.message_type === 'system_health') {
                setStats(data.status);
            }
        });

        return () => unsubscribe();
    }, [client]);

    if (!stats) {
        return (
            <div className="matrix-card p-4 flex items-center justify-center h-full opacity-50 font-lcd animate-pulse">
                INITIALIZING DIAGNOSTICS...
            </div>
        );
    }

    const getStatusColor = (percent: number) => {
        if (percent > 85) return "text-red-500";
        if (percent > 60) return "text-yellow-500";
        return "text-primary";
    };

    return (
        <div className="p-6 space-y-6">
            <div className="grid grid-cols-2 gap-6">
                {/* CPU */}
                <div className="space-y-2">
                    <div className="flex justify-between text-[10px] font-black uppercase">
                        <span className="flex items-center gap-1.5"><Cpu size={12}/> Processor</span>
                        <span className={cn("font-bold tabular-nums", getStatusColor(stats.system.cpu_percent))}>
                            {stats.system.cpu_percent.toFixed(1)}%
                        </span>
                    </div>
                    <div className="h-3 w-full bg-lcd-text/10 border-2 border-lcd-text/20 overflow-hidden">
                        <div 
                            className="h-full bg-primary transition-all duration-1000 shadow-[0_0_10px_var(--lcd-text)]" 
                            style={{ width: `${stats.system.cpu_percent}%` }}
                        />
                    </div>
                </div>

                {/* Memory */}
                <div className="space-y-2">
                    <div className="flex justify-between text-[10px] font-black uppercase">
                        <span className="flex items-center gap-1.5"><Database size={12}/> Memory</span>
                        <span className={cn("font-bold tabular-nums", getStatusColor(stats.system.memory_percent))}>
                            {stats.system.memory_percent.toFixed(1)}%
                        </span>
                    </div>
                    <div className="h-3 w-full bg-lcd-text/10 border-2 border-lcd-text/20 overflow-hidden">
                        <div 
                            className="h-full bg-primary transition-all duration-1000 shadow-[0_0_10px_var(--lcd-text)]" 
                            style={{ width: `${stats.system.memory_percent}%` }}
                        />
                    </div>
                </div>
            </div>

            <div className="grid grid-cols-3 gap-4 border-t-2 border-lcd-text/10 pt-6">
                <div className="flex flex-col">
                    <span className="text-[9px] font-black uppercase opacity-40 mb-1">Active Streams</span>
                    <span className="text-xl font-bold font-lcd tabular-nums tracking-tighter">{stats.application.active_feeds} <span className="text-xs opacity-30">/ {stats.application.total_feeds}</span></span>
                </div>
                <div className="flex flex-col">
                    <span className="text-[9px] font-black uppercase opacity-40 mb-1">WS Uplinks</span>
                    <span className="text-xl font-bold font-lcd tabular-nums tracking-tighter">{stats.application.websocket_clients}</span>
                </div>
                <div className="flex flex-col text-right">
                    <span className="text-[9px] font-black uppercase opacity-40 mb-1">Disk Utilization</span>
                    <span className="text-xl font-bold font-lcd tabular-nums tracking-tighter">{stats.system.disk_percent}%</span>
                </div>
            </div>

            <div className="pt-2 flex items-center justify-between">
                <div className="flex items-center gap-2">
                    <div className="h-2 w-2 rounded-full bg-green-600 animate-pulse" />
                    <span className="text-[9px] font-black uppercase opacity-60">Neural Engine Heartbeat: Nominal</span>
                </div>
                <span className="text-[8px] opacity-30 font-mono">HASH: 0x{Math.random().toString(16).slice(2, 6).toUpperCase()}</span>
            </div>
        </div>
    );
};
