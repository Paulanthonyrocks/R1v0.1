"use client";

import React, { useEffect, useState } from 'react';
import { useWebSocket } from '@/lib/websocket/WebSocketProvider';
import { WebSocketMessageType } from '@/lib/websocket/WebSocketClient';
import { Activity, Cpu, Database, HardDrive, Server, ShieldAlert } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from '@/lib/utils';
import { wsMonitoringService, type ConnectionStats } from '@/lib/services/wsMonitoringService';

// Static display label, generated once: per-render regeneration flickered it.
const HASH_LABEL = Math.random().toString(16).slice(2, 6).toUpperCase();

// REST /websocket/health poll cadence. The WS system_health push covers
// hardware; the connection-manager grid stats only exist via REST.
const WS_HEALTH_POLL_MS = 15000;

const WsGridStat = ({ label, value }: { label: string; value: string }) => (
    <div className="flex flex-col">
        <span className="text-[8px] font-black uppercase opacity-40">{label}</span>
        <span className="text-sm font-bold font-lcd tabular-nums tracking-tighter">{value}</span>
    </div>
);

const WsGridChip = ({ text }: { text: string }) => (
    <span className="text-[8px] font-black uppercase tracking-tighter px-1.5 py-0.5 border border-lcd-text/20 bg-lcd-text/5">{text}</span>
);

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
    const [wsHealth, setWsHealth] = useState<ConnectionStats | null>(null);
    const [wsLinkUp, setWsLinkUp] = useState(true);
    const client = useWebSocket();

    useEffect(() => {
        if (!client) return;

        const unsubscribe = client.subscribe(WebSocketMessageType.GENERAL_NOTIFICATION, (data: any) => {
            if (data.message_type === 'system_health') {
                setStats(data.status);
            }
        });

        return () => unsubscribe();
    }, [client]);

    useEffect(() => {
        let cancelled = false;
        const poll = async () => {
            try {
                const grid = await wsMonitoringService.getHealth();
                if (!cancelled) {
                    setWsHealth(grid);
                    setWsLinkUp(true);
                }
            } catch {
                // Degrade visually (LINK DOWN) instead of spamming toasts from a poller.
                if (!cancelled) setWsLinkUp(false);
            }
        };
        poll();
        const timer = setInterval(poll, WS_HEALTH_POLL_MS);
        return () => {
            cancelled = true;
            clearInterval(timer);
        };
    }, []);

    if (!stats) {
        return (
            <Card className="h-full border-lcd-text/30 bg-industrial-panel flex items-center justify-center opacity-50 font-lcd animate-pulse">
                INITIALIZING DIAGNOSTICS...
            </Card>
        );
    }

    const getStatusColor = (percent: number) => {
        if (percent > 85) return "text-red-500";
        if (percent > 60) return "text-yellow-500";
        return "text-lcd-green";
    };

    return (
        <Card className={cn(
            "relative overflow-hidden border-lcd-text/30 bg-industrial-panel text-lcd-text font-lcd",
            "shadow-[inset_0_0_20px_rgba(0,0,0,0.5)] transition-all duration-500"
        )}>
            {/* Tactical Corner Accents */}
            <div className="absolute top-0 left-0 w-2 h-2 border-t-2 border-l-2 border-lcd-text/50" />
            <div className="absolute top-0 right-0 w-2 h-2 border-t-2 border-r-2 border-lcd-text/50" />
            <div className="absolute bottom-0 left-0 w-2 h-2 border-b-2 border-l-2 border-lcd-text/50" />
            <div className="absolute bottom-0 right-0 w-2 h-2 border-b-2 border-r-2 border-lcd-text/50" />

            <CardHeader className="p-4 pb-2 relative">
                <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-2 text-xs tracking-widest text-lcd-text/70 uppercase">
                        <Activity className="h-3 w-3 animate-pulse" />
                        <span>Hardware Telemetry</span>
                    </div>
                    <div className="text-[10px] px-2 py-0.5 border border-lcd-green/50 bg-lcd-green/10 text-lcd-green font-bold uppercase tracking-tighter">
                        SYSTEM_OK
                    </div>
                </div>
                <CardTitle className="text-lg font-lcd flex items-center gap-2 tracking-tight uppercase">
                    <Server className="h-5 w-5" />
                    CORE SYSTEM STATUS
                </CardTitle>
            </CardHeader>

            <CardContent className="p-4 pt-0">
                <div className="space-y-6">
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
                                    className={cn("h-full transition-all duration-1000 shadow-[0_0_10px_rgba(0,0,0,0.5)]", 
                                        stats.system.cpu_percent > 85 ? "bg-red-500" : stats.system.cpu_percent > 60 ? "bg-yellow-500" : "bg-lcd-green"
                                    )} 
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
                                    className={cn("h-full transition-all duration-1000 shadow-[0_0_10px_rgba(0,0,0,0.5)]", 
                                        stats.system.memory_percent > 85 ? "bg-red-500" : stats.system.memory_percent > 60 ? "bg-yellow-500" : "bg-lcd-green"
                                    )} 
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

                    {/* WS Grid Telemetry — REST /api/v1/websocket/health, 15s poll */}
                    <div className="border-t-2 border-lcd-text/10 pt-4 space-y-3">
                        <div className="flex items-center justify-between">
                            <span className="text-[9px] font-black uppercase opacity-60">WS Grid Telemetry</span>
                            <div className="flex items-center gap-1.5">
                                <div className={cn("h-1.5 w-1.5 rounded-full", wsLinkUp ? "bg-lcd-green animate-pulse" : "bg-red-500")} />
                                <span className={cn("text-[8px] font-black uppercase tracking-tighter", wsLinkUp ? "text-lcd-green/70" : "text-red-500")}>
                                    {wsLinkUp ? "LINK OK" : "LINK DOWN"}
                                </span>
                            </div>
                        </div>
                        {wsHealth ? (
                            <>
                                <div className="grid grid-cols-4 gap-3 text-left">
                                    <WsGridStat label="Clients" value={String(wsHealth.total_connections)} />
                                    <WsGridStat label="Topic Subs" value={String(wsHealth.total_subscriptions)} />
                                    <WsGridStat label="Avg Queue" value={wsHealth.average_queue_size.toFixed(1)} />
                                    <WsGridStat label="Max Queue" value={String(wsHealth.max_queue_size)} />
                                </div>
                                <div className="flex flex-wrap gap-1.5">
                                    {Object.entries(wsHealth.connections_by_role).map(([role, count]) => (
                                        <WsGridChip key={`role-${role}`} text={`${role}: ${count}`} />
                                    ))}
                                    {Object.entries(wsHealth.feed_subscriptions).slice(0, 6).map(([feed, count]) => (
                                        <WsGridChip key={`feed-${feed}`} text={`${feed}: ${count}c`} />
                                    ))}
                                    {Object.keys(wsHealth.feed_subscriptions).length > 6 && (
                                        <WsGridChip text={`+${Object.keys(wsHealth.feed_subscriptions).length - 6} FEEDS`} />
                                    )}
                                    <WsGridChip text={(() => {
                                        const rtt = Object.values(wsHealth.active_client_latencies || {});
                                        if (rtt.length === 0) return 'NO RTT SAMPLES';
                                        const avg = rtt.reduce((a, b) => a + b, 0) / rtt.length;
                                        return `RTT Ø ${avg.toFixed(0)}MS / ${rtt.length} UPLINKS`;
                                    })()} />
                                </div>
                            </>
                        ) : (
                            <div className="text-[9px] font-black uppercase opacity-40 animate-pulse">
                                {wsLinkUp ? "POLLING WS GRID..." : "WS GRID TELEMETRY UNAVAILABLE"}
                            </div>
                        )}
                    </div>

                    <div className="pt-2 flex items-center justify-between">
                        <div className="flex items-center gap-2">
                            <div className="h-2 w-2 rounded-full bg-lcd-green animate-pulse" />
                            <span className="text-[9px] font-black uppercase opacity-60">Neural Engine Heartbeat: Nominal</span>
                        </div>
                        <span className="text-[8px] opacity-30 font-mono uppercase">HASH: 0x{HASH_LABEL}</span>
                    </div>
                </div>
            </CardContent>
        </Card>
    );
};
