"use client";

import React, { useEffect, useState } from 'react';
import DashboardShell from '@/components/dashboard/DashboardShell';
import { Incident, IncidentStatus, IncidentSeverity } from '@/lib/types/incident';
import { AlertTriangle, Calendar, Camera, Maximize2, Trash2, X } from 'lucide-react';
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
import { useWebSocket } from '@/lib/websocket/WebSocketProvider';
import { WebSocketMessageType } from '@/lib/websocket/WebSocketClient';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000";

export default function GalleryPage() {
    const [incidents, setIncidents] = useState<Incident[]>([]);
    const [loading, setLoading] = useState(true);
    const [selectedIncident, setSelectedIncident] = useState<Incident | null>(null);
    const client = useWebSocket();

    const fetchIncidentsWithSnapshots = async () => {
        setLoading(true);
        try {
            // Fetch all incidents that have snapshots
            const res = await fetch(`${API_BASE_URL}/api/v1/incidents`, {
                headers: {
                    'Bypass-Tunnel-Reminder': 'true'
                }
            });
            if (res.ok) {
                const data: Incident[] = await res.json();
                // Filter incidents that have a snapshot path
                const withSnapshots = data
                    .filter(inc => inc.snapshot_path)
                    .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
                setIncidents(withSnapshots);
            }
        } catch (error) {
            console.error("Failed to fetch gallery items:", error);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        fetchIncidentsWithSnapshots();

        // Listen for new snapshots
        const unsubscribe = client.subscribe(WebSocketMessageType.SNAPSHOT_READY, (data: any) => {
            console.log("New snapshot ready received in gallery:", data);
            // Re-fetch to get full incident details
            fetchIncidentsWithSnapshots();
        });

        return () => unsubscribe();
    }, [client]);

    const getSnapshotUrl = (path: string) => {
        if (!path) return "";
        // Backend saves full path or relative path. We served /snapshots/ from backend/data/snapshots
        const filename = path.split('/').pop();
        return `${API_BASE_URL}/snapshots/${filename}`;
    };

    const getSeverityColor = (severity: IncidentSeverity) => {
        switch (severity) {
            case IncidentSeverity.CRITICAL: return "text-red-500 border-red-500";
            case IncidentSeverity.HIGH: return "text-orange-500 border-orange-500";
            case IncidentSeverity.MEDIUM: return "text-yellow-500 border-yellow-500";
            default: return "text-green-500 border-green-500";
        }
    };

    return (
        <DashboardShell>
            <div className="flex flex-col space-y-6">
                <div className="flex justify-between items-center">
                    <div>
                        <h1 className="text-3xl font-bold font-lcd matrix-glow tracking-wider text-lcd-text uppercase">Snapshot Archive</h1>
                        <p className="text-lcd-text/60 font-lcd italic">Visual evidence of detected traffic anomalies</p>
                    </div>
                    <Button onClick={fetchIncidentsWithSnapshots} variant="outline" className="border-lcd-text text-lcd-text hover:bg-lcd-text hover:text-lcd-bg font-lcd">
                        REFRESH RECAP
                    </Button>
                </div>

                {loading && incidents.length === 0 ? (
                    <div className="flex flex-col items-center justify-center py-20 text-lcd-text/50">
                        <Camera className="h-12 w-12 mb-4 animate-pulse" />
                        <p className="font-lcd text-xl">SCANNING DATABASE...</p>
                    </div>
                ) : incidents.length === 0 ? (
                    <div className="text-center py-20 text-lcd-text/50 font-lcd border border-dashed border-lcd-text/30 rounded-lg">
                        <Camera className="h-12 w-12 mx-auto mb-4 opacity-20" />
                        <p>Archive Empty. No visual records found.</p>
                    </div>
                ) : (
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
                        {incidents.map((incident) => (
                            <Card 
                                key={incident.id} 
                                className="matrix-card overflow-hidden group hover:border-lcd-text transition-all duration-300"
                                onClick={() => setSelectedIncident(incident)}
                            >
                                <div className="relative aspect-video bg-black/40 overflow-hidden">
                                    <img 
                                        src={getSnapshotUrl(incident.snapshot_path!)} 
                                        alt={incident.description}
                                        className="w-full h-full object-cover transition-transform duration-500 group-hover:scale-110 opacity-80 group-hover:opacity-100"
                                    />
                                    <div className="absolute inset-0 bg-gradient-to-t from-black/80 via-transparent to-transparent opacity-60" />
                                    
                                    <div className="absolute top-2 right-2">
                                        <Badge variant="outline" className={cn("bg-black/60 font-lcd backdrop-blur-sm", getSeverityColor(incident.severity))}>
                                            {incident.severity}
                                        </Badge>
                                    </div>

                                    <div className="absolute bottom-2 left-2 flex items-center gap-2">
                                        <div className="h-2 w-2 rounded-full bg-red-500 animate-pulse" />
                                        <span className="text-[10px] text-white/70 font-lcd uppercase tracking-tighter">Record #{incident.id.slice(0, 8)}</span>
                                    </div>
                                    
                                    <div className="absolute inset-0 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                                        <div className="bg-lcd-text text-lcd-bg p-2 rounded-full">
                                            <Maximize2 size={20} />
                                        </div>
                                    </div>
                                </div>
                                <CardHeader className="p-3">
                                    <CardTitle className="text-sm font-lcd truncate uppercase tracking-tight text-lcd-text">
                                        {incident.type.replace(/_/g, " ")}
                                    </CardTitle>
                                    <CardDescription className="text-[10px] font-lcd flex items-center gap-1 text-lcd-text/60">
                                        <Calendar size={10} /> {new Date(incident.created_at).toLocaleString()}
                                    </CardDescription>
                                </CardHeader>
                                <CardContent className="p-3 pt-0">
                                    <p className="text-xs font-lcd line-clamp-2 text-lcd-text/80">{incident.description}</p>
                                </CardContent>
                            </Card>
                        ))}
                    </div>
                )}
            </div>

            {/* Modal for Expanded View */}
            {selectedIncident && (
                <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/90 backdrop-blur-sm" onClick={() => setSelectedIncident(null)}>
                    <div 
                        className="relative max-w-5xl w-full bg-lcd-bg border-2 border-lcd-text matrix-glow rounded-none overflow-hidden flex flex-col md:flex-row"
                        onClick={(e) => e.stopPropagation()}
                    >
                        <button 
                            className="absolute top-4 right-4 text-lcd-text hover:scale-110 transition-transform z-10"
                            onClick={() => setSelectedIncident(null)}
                        >
                            <X size={32} />
                        </button>

                        <div className="flex-1 bg-black flex items-center justify-center">
                            <img 
                                src={getSnapshotUrl(selectedIncident.snapshot_path!)} 
                                alt={selectedIncident.description}
                                className="max-h-[80vh] w-full object-contain"
                            />
                        </div>

                        <div className="w-full md:w-80 p-6 border-t-2 md:border-t-0 md:border-l-2 border-lcd-text flex flex-col gap-4">
                            <div>
                                <h2 className="text-2xl font-bold uppercase tracking-wider mb-1">{selectedIncident.type.replace(/_/g, " ")}</h2>
                                <Badge variant="outline" className={cn("font-lcd", getSeverityColor(selectedIncident.severity))}>
                                    {selectedIncident.severity} SEVERITY
                                </Badge>
                            </div>

                            <div className="space-y-4 font-lcd text-sm">
                                <div>
                                    <label className="text-[10px] opacity-40 uppercase block">Timestamp</label>
                                    <p>{new Date(selectedIncident.created_at).toLocaleString()}</p>
                                </div>
                                
                                <div>
                                    <label className="text-[10px] opacity-40 uppercase block">Source Feed</label>
                                    <p className="flex items-center gap-1 italic"><Camera size={12}/> {selectedIncident.feed_id || "SATELLITE/AUTO"}</p>
                                </div>

                                <div>
                                    <label className="text-[10px] opacity-40 uppercase block">Coordinates</label>
                                    <p>{selectedIncident.latitude?.toFixed(4)}, {selectedIncident.longitude?.toFixed(4)}</p>
                                </div>

                                <div>
                                    <label className="text-[10px] opacity-40 uppercase block">Analysis Result</label>
                                    <p className="text-lg leading-snug">{selectedIncident.description}</p>
                                </div>
                            </div>

                            <div className="mt-auto pt-6 flex flex-col gap-2">
                                <Button className="w-full bg-lcd-text text-lcd-bg hover:opacity-90 font-lcd rounded-none uppercase">
                                    Download Evidence
                                </Button>
                                <Button variant="outline" className="w-full border-red-500 text-red-500 hover:bg-red-500 hover:text-white font-lcd rounded-none uppercase">
                                    Flag for Review
                                </Button>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </DashboardShell>
    );
}
