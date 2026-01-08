"use client";

import React, { useEffect, useState } from 'react';
import DashboardShell from '@/components/dashboard/DashboardShell';
import { Camera, MapPin, Navigation, History, Search, ArrowRight, User } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from '@/components/ui/badge';
import { TrafficHeatmap } from '@/components/dashboard/TrafficHeatmap';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

interface GlobalVehicle {
    global_vehicle_id: string;
    last_seen: number;
    feeds_count: number;
}

interface TrackPoint {
    feed_id: string;
    timestamp: number;
    speed: number;
    lane: number;
    direction: string;
    license_plate: string;
}

export default function TrackingPage() {
    const [vehicles, setVehicles] = useState<GlobalVehicle[]>([]);
    const [selectedId, setSelectedId] = useState<string>("");
    const [history, setHistory] = useState<TrackPoint[]>([]);
    const [loading, setLoading] = useState(true);
    const [historyLoading, setHistoryLoading] = useState(false);

    useEffect(() => {
        fetchVehicles();
    }, []);

    const fetchVehicles = async () => {
        setLoading(true);
        try {
            const res = await fetch(`${API_BASE_URL}/api/v1/vehicles/global/list`, {
                headers: {
                    'Bypass-Tunnel-Reminder': 'true'
                }
            });
            if (res.ok) {
                const data = await res.json();
                setVehicles(data);
            }
        } catch (error) {
            console.error("Failed to fetch vehicles:", error);
        } finally {
            setLoading(false);
        }
    };

    const fetchHistory = async (id: string) => {
        setHistoryLoading(true);
        setSelectedId(id);
        try {
            const res = await fetch(`${API_BASE_URL}/api/v1/vehicles/global/${id}/history`, {
                headers: {
                    'Bypass-Tunnel-Reminder': 'true'
                }
            });
            if (res.ok) {
                const data = await res.json();
                setHistory(data);
            }
        } catch (error) {
            console.error("Failed to fetch history:", error);
        } finally {
            setHistoryLoading(false);
        }
    };

    // Group history by feed to show a timeline
    const groupedHistory = history.reduce((acc, point) => {
        const last = acc[acc.length - 1];
        if (last && last.feed_id === point.feed_id) {
            last.points.push(point);
            last.endTime = point.timestamp;
        } else {
            acc.push({
                feed_id: point.feed_id,
                startTime: point.timestamp,
                endTime: point.timestamp,
                points: [point]
            });
        }
        return acc;
    }, [] as any[]);

    return (
        <DashboardShell>
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
                {/* Left Panel: Vehicle List */}
                <div className="lg:col-span-1 space-y-6">
                    <div className="flex flex-col gap-2">
                        <h1 className="text-3xl font-bold font-lcd matrix-glow tracking-wider text-lcd-text uppercase">Identity Tracker</h1>
                        <p className="text-lcd-text/60 font-lcd italic text-sm">Persistent ReID over distributed nodes</p>
                    </div>

                    <Card className="matrix-card">
                        <CardHeader className="pb-3 border-b border-lcd-text/10">
                            <div className="relative">
                                <Search className="absolute left-2 top-2.5 h-4 w-4 text-lcd-text/50" />
                                <Input
                                    placeholder="Search Global ID..."
                                    className="pl-8 bg-black/20 border-lcd-text/30 text-lcd-text font-lcd h-9"
                                />
                            </div>
                        </CardHeader>
                        <CardContent className="p-0 max-h-[600px] overflow-y-auto custom-scrollbar">
                            {loading ? (
                                <div className="p-10 text-center font-lcd opacity-50 animate-pulse">SYNCING DATA...</div>
                            ) : vehicles.length === 0 ? (
                                <div className="p-10 text-center font-lcd opacity-30">NO PERSISTENT ENTITIES FOUND</div>
                            ) : (
                                <div className="divide-y divide-lcd-text/5">
                                    {vehicles.map((v) => (
                                        <div 
                                            key={v.global_vehicle_id}
                                            className={cn(
                                                "p-4 cursor-pointer hover:bg-lcd-text/5 transition-colors group flex justify-between items-center",
                                                selectedId === v.global_vehicle_id && "bg-lcd-text/10 border-l-2 border-lcd-text"
                                            )}
                                            onClick={() => fetchHistory(v.global_vehicle_id)}
                                        >
                                            <div className="space-y-1">
                                                <div className="font-bold font-lcd text-lcd-text group-hover:matrix-glow transition-all">
                                                    {v.global_vehicle_id}
                                                </div>
                                                <div className="text-[10px] opacity-50 font-lcd uppercase">
                                                    Nodes Visited: {v.feeds_count}
                                                </div>
                                            </div>
                                            <div className="text-right">
                                                <div className="text-[10px] opacity-40 font-lcd">
                                                    Last Seen: {new Date(v.last_seen * 1000).toLocaleTimeString()}
                                                </div>
                                                <ArrowRight size={14} className="ml-auto opacity-0 group-hover:opacity-100 transition-opacity mt-1" />
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </CardContent>
                    </Card>
                </div>

                {/* Right Panel: History Timeline */}
                <div className="lg:col-span-2 space-y-6">
                    {!selectedId ? (
                        <div className="h-full flex flex-col items-center justify-center py-20 text-lcd-text/20 font-lcd border-2 border-dashed border-lcd-text/10 rounded-lg">
                            <History size={64} className="mb-4 opacity-10" />
                            <p className="text-xl tracking-widest">SELECT ENTITY TO VIEW TRAJECTORY</p>
                        </div>
                    ) : historyLoading ? (
                        <div className="h-full flex flex-col items-center justify-center py-20 text-lcd-text/50 font-lcd">
                            <div className="h-12 w-12 border-4 border-lcd-text/20 border-t-lcd-text rounded-full animate-spin mb-4" />
                            <p className="tracking-widest uppercase">Retrieving trajectory logs...</p>
                        </div>
                    ) : (
                        <div className="space-y-6 animate-in fade-in slide-in-from-right-4 duration-500">
                            <div className="flex justify-between items-end border-b-2 border-lcd-text/20 pb-4">
                                <div>
                                    <h2 className="text-2xl font-bold font-lcd text-lcd-text tracking-widest uppercase">TRAJECTORY LOG: {selectedId}</h2>
                                    <p className="text-xs text-lcd-text/50 font-lcd uppercase mt-1">Cross-Node analysis for session period</p>
                                </div>
                                <Badge variant="outline" className="font-lcd bg-lcd-text/5 text-primary border-primary/50">
                                    VERIFIED IDENTITY
                                </Badge>
                            </div>

                            {/* Trajectory Heatmap */}
                            <div className="matrix-card p-4">
                                <div className="flex justify-between items-center mb-4">
                                    <h3 className="text-sm font-bold uppercase tracking-widest font-lcd">Activity Heatmap</h3>
                                    <span className="text-[10px] opacity-40 uppercase font-lcd italic text-primary">Spatial Path Analysis</span>
                                </div>
                                <TrafficHeatmap global_id={selectedId} hours={24} height={300} />
                            </div>

                            <div className="relative space-y-8 before:absolute before:inset-0 before:ml-5 before:-translate-x-px md:before:ml-[2.25rem] md:before:translate-x-0 before:h-full before:w-0.5 before:bg-gradient-to-b before:from-transparent before:via-lcd-text/20 before:to-transparent">
                                {groupedHistory.map((group, idx) => (
                                    <div key={idx} className="relative flex items-start group">
                                        <div className="absolute left-0 mt-1 md:mt-0 flex items-center justify-center w-10 h-10 rounded-full border-2 border-lcd-text bg-lcd-bg shadow-sm z-10 group-hover:matrix-glow transition-all">
                                            <Camera size={18} />
                                        </div>
                                        <div className="flex-1 ml-14 md:ml-16">
                                            <Card className="matrix-card group-hover:bg-lcd-text/[0.02] transition-colors">
                                                <CardHeader className="p-4 pb-2 flex flex-row items-center justify-between">
                                                    <div>
                                                        <CardTitle className="text-lg font-lcd text-primary">{group.feed_id.replace('Feed_', 'NODE ')}</CardTitle>
                                                        <CardDescription className="text-[10px] font-lcd uppercase">
                                                            {new Date(group.startTime * 1000).toLocaleTimeString()} - {new Date(group.endTime * 1000).toLocaleTimeString()}
                                                        </CardDescription>
                                                    </div>
                                                    <div className="text-right">
                                                        <span className="text-[10px] opacity-40 block uppercase">Avg. Speed</span>
                                                        <span className="text-lg font-bold font-lcd">
                                                            {(group.points.reduce((s: any, p: any) => s + p.speed, 0) / group.points.length).toFixed(1)} km/h
                                                        </span>
                                                    </div>
                                                </CardHeader>
                                                <CardContent className="p-4 pt-0">
                                                    <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mt-2">
                                                        <div className="flex items-center gap-2 text-xs opacity-70">
                                                            <Navigation size={12} /> {group.points[0].direction}
                                                        </div>
                                                        <div className="flex items-center gap-2 text-xs opacity-70">
                                                            <MapPin size={12} /> Lane {group.points[0].lane}
                                                        </div>
                                                        {group.points[0].license_plate !== "Unknown" && (
                                                            <div className="flex items-center gap-2 text-xs opacity-70 font-bold text-primary">
                                                                <User size={12} /> {group.points[0].license_plate}
                                                            </div>
                                                        )}
                                                    </div>
                                                </CardContent>
                                            </Card>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </DashboardShell>
    );
}
