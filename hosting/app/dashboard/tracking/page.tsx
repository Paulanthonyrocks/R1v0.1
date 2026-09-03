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
import { useAuth } from '@/lib/auth/AuthProvider';
import AuthGuard from '@/components/auth/AuthGuard';
import { APIClient } from '@/lib/api/APIClient';
import { getBackendBaseURL } from '@/lib/api/backendBaseUrl';

const API_BASE_URL = getBackendBaseURL();

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
    const { token } = useAuth();

    useEffect(() => {
        if (token) fetchVehicles();
    }, [token]);

    const fetchVehicles = async () => {
        setLoading(true);
        try {
            const apiClient = APIClient.getInstance({ baseURL: API_BASE_URL });
            const data = await apiClient.get<GlobalVehicle[]>('/api/v1/vehicles/global/list');
            setVehicles(data);
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
            const apiClient = APIClient.getInstance({ baseURL: API_BASE_URL });
            const data = await apiClient.get<TrackPoint[]>(`/api/v1/vehicles/global/${id}/history`);
            setHistory(data);
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
        <AuthGuard>
        <DashboardShell>
            <div className="retro-title-container">
                <div className="flex flex-col md:flex-row justify-between items-end gap-4">
                    <div>
                        <h1 className="text-5xl font-black uppercase tracking-tighter font-lcd matrix-glow text-lcd-text mb-1">Identity Tracker</h1>
                        <div className="flex items-center gap-2">
                            <span className="terminal-text text-[10px]">REID.PERSISTENCE.ENABLED // TRACKING_DISTRIBUTED_ENTITIES</span>
                        </div>
                    </div>
                    <div className="flex bg-lcd-text/5 px-4 py-2 border-2 border-lcd-text font-bold text-[10px] uppercase tracking-widest items-center gap-4">
                        <div className="flex items-center gap-2">
                            <div className="h-2 w-2 rounded-full bg-primary animate-pulse" />
                            Global ReID Buffer: {vehicles.length} Objects
                        </div>
                    </div>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-12">
                {/* Left Panel: Vehicle List */}
                <div className="lg:col-span-1 space-y-8">
                    <Card className="matrix-card p-0 overflow-hidden">
                        <div className="matrix-card-header bg-lcd-text/10">
                            <div className="flex items-center gap-2">
                                <Search size={14} />
                                <span>Registry Filter // Query Active IDs</span>
                            </div>
                        </div>
                        <div className="p-4 bg-lcd-text/5">
                            <div className="relative group">
                                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-lcd-text/40 group-focus-within:text-lcd-text transition-colors" />
                                <Input
                                    placeholder="SEARCH_BY_ID..."
                                    className="pl-10 bg-black/10 border-2 border-lcd-text/20 text-lcd-text font-lcd h-12 uppercase focus-visible:ring-lcd-text transition-all"
                                />
                            </div>
                        </div>
                        <div className="max-h-[600px] overflow-y-auto custom-scrollbar border-t-2 border-lcd-text/10">
                            {loading ? (
                                <div className="p-20 text-center font-black opacity-30 animate-pulse text-sm">SYNCHRONIZING REGISTRY...</div>
                            ) : vehicles.length === 0 ? (
                                <div className="p-20 text-center font-black opacity-10 text-sm">NO_ENTITIES_IN_BUFFER</div>
                            ) : (
                                <div className="divide-y-2 divide-lcd-text/5">
                                    {vehicles.map((v) => (
                                        <div 
                                            key={v.global_vehicle_id}
                                            className={cn(
                                                "p-5 cursor-pointer transition-all group flex justify-between items-center",
                                                selectedId === v.global_vehicle_id 
                                                    ? "bg-lcd-text text-lcd-bg shadow-lg" 
                                                    : "hover:bg-lcd-text/5"
                                            )}
                                            onClick={() => fetchHistory(v.global_vehicle_id)}
                                        >
                                            <div className="space-y-1">
                                                <div className="font-black text-xl tracking-tighter uppercase transition-all">
                                                    {v.global_vehicle_id.slice(0, 16)}...
                                                </div>
                                                <div className={cn("text-[9px] font-black uppercase opacity-60", selectedId === v.global_vehicle_id && "opacity-100")}>
                                                    Surveillance Nodes Visited: {v.feeds_count}
                                                </div>
                                            </div>
                                            <div className="text-right">
                                                <div className={cn("text-[9px] font-black opacity-40 uppercase tabular-nums", selectedId === v.global_vehicle_id && "opacity-60")}>
                                                    {new Date(v.last_seen * 1000).toLocaleTimeString()}
                                                </div>
                                                <ArrowRight size={18} className={cn("ml-auto opacity-0 group-hover:opacity-100 transition-all mt-1", selectedId === v.global_vehicle_id && "opacity-100")} />
                                            </div>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </div>
                    </Card>
                </div>

                {/* Right Panel: History Timeline */}
                <div className="lg:col-span-2 space-y-8">
                    {!selectedId ? (
                        <div className="h-full flex flex-col items-center justify-center py-32 text-lcd-text/20 font-black border-4 border-dashed border-lcd-text/10 bg-lcd-text/5 group">
                            <History size={80} className="mb-6 opacity-5 group-hover:opacity-10 transition-opacity" />
                            <p className="text-2xl tracking-[0.4em] uppercase">Select Entity to Reconstruction Trajectory</p>
                        </div>
                    ) : historyLoading ? (
                        <div className="h-full flex flex-col items-center justify-center py-32 text-lcd-text/50 font-black">
                            <div className="h-16 w-16 border-4 border-lcd-text/20 border-t-lcd-text rounded-full animate-spin mb-6" />
                            <p className="tracking-[0.2em] uppercase text-xl">Reconstructing Trajectory Logs...</p>
                        </div>
                    ) : (
                        <div className="space-y-12 animate-in fade-in slide-in-from-right-8 duration-500">
                            <div className="flex justify-between items-end border-b-4 border-lcd-text pb-4">
                                <div>
                                    <h2 className="text-3xl font-black text-lcd-text tracking-tighter uppercase">Trajectory Log // {selectedId}</h2>
                                    <p className="text-[10px] font-bold text-lcd-text/50 uppercase mt-1">Unified Session Reconstruction // Verified Identity Protocol</p>
                                </div>
                                <div className="bg-primary text-secondary px-4 py-1 font-black text-[10px] tracking-widest uppercase">
                                    ID_VERIFIED
                                </div>
                            </div>

                            {/* Trajectory Heatmap */}
                            <div className="matrix-card p-0 overflow-hidden">
                                <div className="matrix-card-header bg-lcd-text/10">
                                    <div className="flex items-center gap-2">
                                        <MapPin size={14} />
                                        <span>Spatial Path Analysis // Density Recon</span>
                                    </div>
                                </div>
                                <div className="p-4 bg-lcd-text/5">
                                    <TrafficHeatmap global_id={selectedId} hours={24} height={350} />
                                </div>
                            </div>

                            <div className="relative space-y-12 before:absolute before:inset-0 before:ml-6 before:-translate-x-px md:before:ml-[2.25rem] md:before:translate-x-0 before:h-full before:w-1 before:bg-gradient-to-b before:from-transparent before:via-lcd-text/30 before:to-transparent">
                                {groupedHistory.map((group, idx) => (
                                    <div key={idx} className="relative flex items-start group">
                                        <div className="absolute left-0 mt-1 md:mt-0 flex items-center justify-center w-12 h-12 border-4 border-lcd-text bg-lcd-bg z-10 group-hover:bg-lcd-text group-hover:text-lcd-bg transition-all">
                                            <Camera size={24} />
                                        </div>
                                        <div className="flex-1 ml-16 md:ml-20">
                                            <div className="matrix-card p-0 hover:translate-x-2 transition-transform cursor-default">
                                                <div className="matrix-card-header">
                                                    <div>
                                                        <span className="text-xl font-black tracking-tighter text-primary group-hover:text-lcd-bg">
                                                            {group.feed_id.toUpperCase().replace('_', ' ')}
                                                        </span>
                                                        <div className="text-[9px] font-bold opacity-40 group-hover:opacity-60 group-hover:text-lcd-bg">
                                                            ENTRY: {new Date(group.startTime * 1000).toLocaleTimeString()} // EXIT: {new Date(group.endTime * 1000).toLocaleTimeString()}
                                                        </div>
                                                    </div>
                                                    <div className="text-right">
                                                        <span className="text-[9px] font-black opacity-40 uppercase block group-hover:text-lcd-bg">Avg. Velocity</span>
                                                        <span className="text-2xl font-black font-lcd tabular-nums group-hover:text-lcd-bg">
                                                            {(group.points.reduce((s: any, p: any) => s + p.speed, 0) / group.points.length).toFixed(1)} <span className="text-xs">km/h</span>
                                                        </span>
                                                    </div>
                                                </div>
                                                <div className="p-6 grid grid-cols-2 md:grid-cols-4 gap-6 text-xs font-bold uppercase tracking-tight group-hover:text-lcd-bg">
                                                    <div className="flex items-center gap-2 opacity-70">
                                                        <Navigation size={14} /> HEADING: {group.points[0].direction}
                                                    </div>
                                                    <div className="flex items-center gap-2 opacity-70">
                                                        <MapPin size={14} /> LANE_SEG: {group.points[0].lane}
                                                    </div>
                                                    {group.points[0].license_plate !== "Unknown" && (
                                                        <div className="flex items-center gap-2 text-primary group-hover:text-lcd-bg">
                                                            <User size={14} /> OCR_DATA: {group.points[0].license_plate}
                                                        </div>
                                                    )}
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </DashboardShell>
        </AuthGuard>
    );
}
