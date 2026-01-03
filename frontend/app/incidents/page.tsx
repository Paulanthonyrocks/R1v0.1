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

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

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
            const res = await fetch(url.toString());
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
                headers: { 'Content-Type': 'application/json' },
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
            <div className="flex flex-col space-y-6">
                <div className="flex justify-between items-center">
                    <div>
                        <h1 className="text-3xl font-bold font-lcd matrix-glow tracking-wider text-lcd-text">INCIDENT COMMAND</h1>
                        <p className="text-lcd-text/60 font-lcd">Monitor and manage active traffic incidents</p>
                    </div>
                    <Button onClick={fetchIncidents} variant="outline" className="border-lcd-text text-lcd-text hover:bg-lcd-text hover:text-lcd-bg font-lcd">
                        REFRESH DATA
                    </Button>
                </div>

                {/* Filters */}
                <div className="flex gap-4 p-4 matrix-card rounded-lg items-center">
                    <div className="relative flex-1">
                        <Search className="absolute left-2 top-2.5 h-4 w-4 text-lcd-text/50" />
                        <Input
                            placeholder="Search incidents..."
                            className="pl-8 bg-black/20 border-lcd-text/30 text-lcd-text font-lcd"
                            value={searchTerm}
                            onChange={(e) => setSearchTerm(e.target.value)}
                        />
                    </div>
                    <Select value={statusFilter} onValueChange={setStatusFilter}>
                        <SelectTrigger className="w-[180px] bg-black/20 border-lcd-text/30 text-lcd-text font-lcd">
                            <SelectValue placeholder="Filter by Status" />
                        </SelectTrigger>
                        <SelectContent className="bg-lcd-bg border-lcd-text text-lcd-text font-lcd">
                            <SelectItem value="ALL">All Statuses</SelectItem>
                            <SelectItem value={IncidentStatus.NEW}>New</SelectItem>
                            <SelectItem value={IncidentStatus.ACKNOWLEDGED}>Acknowledged</SelectItem>
                            <SelectItem value={IncidentStatus.RESOLVED}>Resolved</SelectItem>
                        </SelectContent>
                    </Select>
                </div>

                {/* Incident List */}
                <div className="grid gap-4">
                    {loading && incidents.length === 0 ? (
                         <div className="text-center py-20 text-lcd-text/50 font-lcd">Loading incidents...</div>
                    ) : filteredIncidents.length === 0 ? (
                        <div className="text-center py-20 text-lcd-text/50 font-lcd border border-dashed border-lcd-text/30 rounded-lg">
                            No incidents found matching your criteria.
                        </div>
                    ) : (
                        filteredIncidents.map((incident) => (
                            <Card key={incident.id} className="matrix-card border-l-4 border-l-lcd-text hover:bg-lcd-text/5 transition-colors">
                                <CardHeader className="pb-2">
                                    <div className="flex justify-between items-start">
                                        <div className="space-y-1">
                                            <CardTitle className="text-xl font-lcd tracking-wide flex items-center gap-2">
                                                {incident.type.replace(/_/g, " ")}
                                                <Badge variant="outline" className={cn("font-lcd ml-2", getSeverityColor(incident.severity))}>
                                                    {incident.severity}
                                                </Badge>
                                            </CardTitle>
                                            <CardDescription className="text-lcd-text/70 font-lcd">
                                                Reported: {new Date(incident.created_at).toLocaleString()}
                                            </CardDescription>
                                        </div>
                                        <div className={cn("font-bold font-lcd", getStatusColor(incident.status))}>
                                            {incident.status}
                                        </div>
                                    </div>
                                </CardHeader>
                                <CardContent>
                                    <div className="grid md:grid-cols-3 gap-6">
                                        {incident.snapshot_path && (
                                            <div className="relative group cursor-zoom-in aspect-video bg-black/20 border border-lcd-text/20 overflow-hidden">
                                                <img 
                                                    src={getSnapshotUrl(incident.snapshot_path)} 
                                                    alt="Incident Evidence" 
                                                    className="w-full h-full object-cover transition-transform group-hover:scale-105"
                                                />
                                                <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 bg-black/40 transition-opacity">
                                                    <span className="text-[10px] font-lcd text-white uppercase tracking-widest">View Full Record</span>
                                                </div>
                                            </div>
                                        )}
                                        <div className={cn(incident.snapshot_path ? "md:col-span-1" : "md:col-span-2")}>
                                            <p className="text-lg mb-2">{incident.description}</p>
                                            <div className="space-y-1">
                                                {incident.feed_id && (
                                                    <div className="text-sm opacity-60 flex items-center gap-1">
                                                        <AlertTriangle size={14} /> Source: {incident.feed_id}
                                                    </div>
                                                )}
                                                {incident.latitude && (
                                                    <div className="text-sm opacity-60 font-lcd">
                                                        LOC: {incident.latitude.toFixed(4)}, {incident.longitude?.toFixed(4)}
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                        <div className="flex justify-end items-center gap-2">
                                            {incident.status === IncidentStatus.NEW && (
                                                <Button 
                                                    size="sm"
                                                    onClick={() => handleUpdateStatus(incident.id, IncidentStatus.ACKNOWLEDGED)}
                                                    className="bg-yellow-500/20 text-yellow-500 border border-yellow-500 hover:bg-yellow-500 hover:text-black font-lcd"
                                                >
                                                    <Clock className="mr-2 h-4 w-4" /> Acknowledge
                                                </Button>
                                            )}
                                            {incident.status !== IncidentStatus.RESOLVED && (
                                                <Button 
                                                    size="sm"
                                                    onClick={() => handleUpdateStatus(incident.id, IncidentStatus.RESOLVED)}
                                                    className="bg-green-500/20 text-green-500 border border-green-500 hover:bg-green-500 hover:text-black font-lcd"
                                                >
                                                    <CheckCircle className="mr-2 h-4 w-4" /> Resolve
                                                </Button>
                                            )}
                                        </div>
                                    </div>
                                </CardContent>
                            </Card>
                        ))
                    )}
                </div>
            </div>
        </DashboardShell>
    );
}
