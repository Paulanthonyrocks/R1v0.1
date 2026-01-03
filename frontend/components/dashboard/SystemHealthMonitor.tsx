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
        <div className="matrix-card p-4 space-y-4">
            <div className="flex justify-between items-center border-b border-lcd-text/10 pb-2">
                <h3 className="text-sm font-bold uppercase tracking-widest flex items-center gap-2">
                    <Server size={16} /> System Diagnostics
                </h3>
                <span className="text-[10px] opacity-40 font-lcd">
                    REFRESH: 10S
                </span>
            </div>

            <div className="grid grid-cols-2 gap-4">
                {/* CPU */}
                <div className="space-y-1">
                    <div className="flex justify-between text-[10px] uppercase">
                        <span className="flex items-center gap-1"><Cpu size={10}/> Processor</span>
                        <span className={cn("font-bold", getStatusColor(stats.system.cpu_percent))}>
                            {stats.system.cpu_percent.toFixed(1)}%
                        </span>
                    </div>
                    <div className="h-1.5 w-full bg-lcd-text/10 rounded-full overflow-hidden">
                        <div 
                            className="h-full bg-primary transition-all duration-1000" 
                            style={{ width: `${stats.system.cpu_percent}%` }}
                        />
                    </div>
                </div>

                {/* Memory */}
                <div className="space-y-1">
                    <div className="flex justify-between text-[10px] uppercase">
                        <span className="flex items-center gap-1"><Database size={10}/> Memory</span>
                        <span className={cn("font-bold", getStatusColor(stats.system.memory_percent))}>
                            {stats.system.memory_percent.toFixed(1)}%
                        </span>
                    </div>
                    <div className="h-1.5 w-full bg-lcd-text/10 rounded-full overflow-hidden">
                        <div 
                            className="h-full bg-primary transition-all duration-1000" 
                            style={{ width: `${stats.system.memory_percent}%` }}
                        />
                    </div>
                </div>
            </div>

            <div className="grid grid-cols-3 gap-2 border-t border-lcd-text/5 pt-3">
                <div className="flex flex-col">
                    <span className="text-[8px] uppercase opacity-50">Active Feeds</span>
                    <span className="text-sm font-bold font-lcd">{stats.application.active_feeds}/{stats.application.total_feeds}</span>
                </div>
                <div className="flex flex-col">
                    <span className="text-[8px] uppercase opacity-50">WS Clients</span>
                    <span className="text-sm font-bold font-lcd">{stats.application.websocket_clients}</span>
                </div>
                <div className="flex flex-col">
                    <span className="text-[8px] uppercase opacity-50">Disk Load</span>
                    <span className="text-sm font-bold font-lcd">{stats.system.disk_percent}%</span>
                </div>
            </div>

            <div className="pt-1 flex items-center gap-2">
                <div className="h-1 w-1 rounded-full bg-primary animate-ping" />
                <span className="text-[8px] opacity-30 uppercase font-lcd">Telemetry Link Stable</span>
            </div>
        </div>
    );
};
