"use client";

import React, { useEffect, useState } from 'react';
import DashboardShell from '@/components/dashboard/DashboardShell';
import { Incident, IncidentStatus, IncidentSeverity } from '@/lib/types/incident';
import { AlertTriangle, CheckCircle, Clock, Search, Filter } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export default function IncidentsPage() {
    const [incidents, setIncidents] = useState<Incident[]>([]);
    const [loading, setLoading] = useState(true);
    const [statusFilter, setStatusFilter] = useState<string>("ALL");
    const [searchTerm, setSearchTerm] = useState("");

    const fetchIncidents = async () => {
        setLoading(true);
        try {
            const url = new URL(`${API_BASE_URL}/api/v1/incidents`);
            if (statusFilter !== "ALL") {
                url.searchParams.append("status", statusFilter);
            }
            const res = await fetch(url.toString(), {
                headers: {
                    'Bypass-Tunnel-Reminder': 'true'
                }
            });
            if (res.ok) {
                const data = await res.json();
                setIncidents(data);
            }
        } catch (error) {
            console.error("Failed to fetch incidents:", error);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchIncidents();
        // Refresh every 30s
        const interval = setInterval(fetchIncidents, 30000);
        return () => clearInterval(interval);
    }, [statusFilter]);

    const handleUpdateStatus = async (id: string, newStatus: IncidentStatus) => {
        try {
            const res = await fetch(`${API_BASE_URL}/api/v1/incidents/${id}`, {
                method: 'PATCH',
                headers: { 
                    'Content-Type': 'application/json',
                    'Bypass-Tunnel-Reminder': 'true'
                },
                body: JSON.stringify({ status: newStatus })
            });
            if (res.ok) {
                // Optimistic update
                setIncidents(prev => prev.map(inc => 
                    inc.id === id ? { ...inc, status: newStatus } : inc
                ));
            }
        } catch (error) {
            console.error("Failed to update status:", error);
        }
    };

    const getSeverityColor = (severity: IncidentSeverity) => {
        switch (severity) {
            case IncidentSeverity.CRITICAL: return "bg-red-500/20 text-red-500 border-red-500";
            case IncidentSeverity.HIGH: return "bg-orange-500/20 text-orange-500 border-orange-500";
            case IncidentSeverity.MEDIUM: return "bg-yellow-500/20 text-yellow-500 border-yellow-500";
            default: return "bg-green-500/20 text-green-500 border-green-500";
        }
    };

    const getStatusColor = (status: IncidentStatus) => {
        switch (status) {
            case IncidentStatus.NEW: return "text-red-400";
            case IncidentStatus.ACKNOWLEDGED: return "text-yellow-400";
            case IncidentStatus.RESOLVED: return "text-green-400";
            default: return "text-gray-400";
        }
    };

    const getSnapshotUrl = (path: string) => {
        if (!path) return "";
        const filename = path.split('/').pop();
        return `${API_BASE_URL}/snapshots/${filename}`;
    };

    const filteredIncidents = incidents.filter(inc => 
        inc.description.toLowerCase().includes(searchTerm.toLowerCase()) ||
        inc.type.toLowerCase().includes(searchTerm.toLowerCase())
    );

    return (
        <DashboardShell>
            <div className="retro-title-container">
                <div className="flex flex-col md:flex-row justify-between items-end gap-4">
                    <div>
                        <h1 className="text-5xl font-black uppercase tracking-tighter font-lcd matrix-glow text-lcd-text mb-1">Incident Command</h1>
                        <div className="flex items-center gap-2">
                            <span className="terminal-text text-[10px]">AUTH.OPERATOR_SECURE // INCIDENT_PROTOCOLS_ENABLED</span>
                        </div>
                    </div>
                    <Button onClick={fetchIncidents} className="matrix-btn-sleek h-12 px-8">
                        <RefreshCw className={cn("mr-2 h-4 w-4", loading && "animate-spin")} />
                        RESYNC DATABASE
                    </Button>
                </div>
            </div>

            {/* Filters Bar */}
            <div className="matrix-card p-0 mb-8 overflow-hidden">
                <div className="matrix-card-header bg-lcd-text/10">
                    <div className="flex items-center gap-2">
                        <Filter size={14} />
                        <span>Filter Matrix // Registry Query</span>
                    </div>
                </div>
                <div className="p-4 flex flex-col md:flex-row gap-6 items-center bg-lcd-text/5">
                    <div className="relative flex-1 w-full">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-lcd-text/50" />
                        <Input
                            placeholder="QUERY BY DESCRIPTION OR CLASSIFICATION..."
                            className="pl-10 bg-black/10 border-2 border-lcd-text/30 text-lcd-text font-lcd h-12 uppercase placeholder:text-lcd-text/30 focus-visible:ring-lcd-text"
                            value={searchTerm}
                            onChange={(e) => setSearchTerm(e.target.value)}
                        />
                    </div>
                    <Select value={statusFilter} onValueChange={setStatusFilter}>
                        <SelectTrigger className="w-full md:w-[240px] bg-black/10 border-2 border-lcd-text/30 text-lcd-text font-lcd h-12 uppercase">
                            <SelectValue placeholder="STATUS" />
                        </SelectTrigger>
                        <SelectContent className="bg-lcd-bg border-2 border-lcd-text text-lcd-text font-lcd">
                            <SelectItem value="ALL">ALL REGISTRIES</SelectItem>
                            <SelectItem value={IncidentStatus.NEW}>UNRESOLVED (NEW)</SelectItem>
                            <SelectItem value={IncidentStatus.ACKNOWLEDGED}>PENDING (ACK)</SelectItem>
                            <SelectItem value={IncidentStatus.RESOLVED}>ARCHIVED (RESOLVED)</SelectItem>
                        </SelectContent>
                    </Select>
                </div>
            </div>

            {/* Incident List */}
            <div className="space-y-6">
                {loading && incidents.length === 0 ? (
                    <div className="matrix-card py-24 flex flex-col items-center justify-center border-dashed border-4 opacity-30">
                         <Loader2 className="animate-spin h-12 w-12 mb-4" />
                         <p className="tracking-[0.5em] font-black text-2xl uppercase">Polling Active Threads...</p>
                    </div>
                ) : filteredIncidents.length === 0 ? (
                    <div className="matrix-card py-24 flex flex-col items-center justify-center border-dashed border-4 opacity-30">
                        <ShieldCheck size={48} className="mb-4" />
                        <p className="tracking-[0.3em] font-black text-xl uppercase text-center">No Incidents Found // Area Clear</p>
                    </div>
                ) : (
                    filteredIncidents.map((incident) => (
                        <div key={incident.id} className="matrix-card p-0 group overflow-hidden border-l-[12px] border-l-lcd-text">
                            <div className="matrix-card-header bg-lcd-text/5">
                                <div className="flex items-center gap-4">
                                    <span className="text-lg font-black tracking-tighter">
                                        {incident.type.replace(/_/g, " ")}
                                    </span>
                                    <div className={cn(
                                        "px-2 py-0.5 text-[10px] font-black uppercase border-2",
                                        incident.severity === IncidentSeverity.CRITICAL ? "severity-critical" :
                                        incident.severity === IncidentSeverity.HIGH ? "severity-high" :
                                        incident.severity === IncidentSeverity.MEDIUM ? "severity-medium" : "severity-low"
                                    )}>
                                        {incident.severity}
                                    </div>
                                </div>
                                <div className={cn("font-black tracking-widest text-[10px] px-3 py-1 border-2", 
                                    incident.status === IncidentStatus.NEW ? "bg-red-600/10 text-red-600 border-red-600" :
                                    incident.status === IncidentStatus.ACKNOWLEDGED ? "bg-yellow-500/10 text-yellow-600 border-yellow-600" :
                                    "bg-green-600/10 text-green-700 border-green-700"
                                )}>
                                    {incident.status.toUpperCase()}
                                </div>
                            </div>
                            
                            <div className="p-6 grid md:grid-cols-4 gap-8 bg-lcd-text/[0.02]">
                                {incident.snapshot_path && (
                                    <div className="md:col-span-1">
                                        <div className="relative group cursor-zoom-in aspect-square bg-black/20 border-2 border-lcd-text overflow-hidden shadow-inner">
                                            <img 
                                                src={getSnapshotUrl(incident.snapshot_path)} 
                                                alt="Evidence" 
                                                className="w-full h-full object-cover transition-all group-hover:scale-110 filter grayscale group-hover:grayscale-0"
                                            />
                                            <div className="absolute inset-0 bg-lcd-text/10 group-hover:bg-transparent transition-colors" />
                                            <div className="absolute bottom-2 right-2 bg-black/80 text-white text-[8px] px-1.5 py-0.5 font-lcd">
                                                EVIDENCE_01
                                            </div>
                                        </div>
                                    </div>
                                )}
                                
                                <div className={cn("flex flex-col justify-between", incident.snapshot_path ? "md:col-span-2" : "md:col-span-3")}>
                                    <div className="space-y-4">
                                        <p className="text-2xl font-bold leading-tight uppercase tracking-tight">{incident.description}</p>
                                        <div className="grid grid-cols-2 gap-4 text-[10px] font-bold opacity-60">
                                            <div className="flex items-center gap-2 uppercase">
                                                <AlertTriangle size={14} /> Node_ID: {incident.feed_id}
                                            </div>
                                            <div className="flex items-center gap-2 uppercase">
                                                <Clock size={14} /> Reported: {new Date(incident.created_at).toLocaleTimeString()}
                                            </div>
                                            {incident.latitude && (
                                                <div className="flex items-center gap-2 uppercase">
                                                    <MapPin size={14} /> LOC: {incident.latitude.toFixed(4)}N, {incident.longitude?.toFixed(4)}E
                                                </div>
                                            )}
                                        </div>
                                    </div>
                                    
                                    <div className="pt-4 border-t border-lcd-text/10 mt-4 flex gap-3">
                                        {incident.status === IncidentStatus.NEW && (
                                            <Button 
                                                onClick={() => handleUpdateStatus(incident.id, IncidentStatus.ACKNOWLEDGED)}
                                                className="matrix-btn-sleek bg-yellow-400 text-black border-yellow-600 hover:bg-yellow-500 h-10 px-6 text-xs"
                                            >
                                                <Clock className="mr-2 h-4 w-4" /> Acknowledge
                                            </Button>
                                        )}
                                        {incident.status !== IncidentStatus.RESOLVED && (
                                            <Button 
                                                onClick={() => handleUpdateStatus(incident.id, IncidentStatus.RESOLVED)}
                                                className="matrix-btn-sleek bg-green-500 text-black border-green-700 hover:bg-green-600 h-10 px-6 text-xs"
                                            >
                                                <CheckCircle className="mr-2 h-4 w-4" /> Resolve & Archive
                                            </Button>
                                        )}
                                        <Button 
                                            variant="outline"
                                            className="matrix-btn-sleek h-10 border-lcd-text/30 text-lcd-text/50 hover:text-lcd-text px-4"
                                        >
                                            View Logs
                                        </Button>
                                    </div>
                                </div>
                            </div>
                        </div>
                    ))
                )}
            </div>
        </DashboardShell>
    );
}
