"use client";

import React, { useState, useMemo } from 'react';
import AuthGuard from '@/components/auth/AuthGuard';
import DashboardShell from '@/components/dashboard/DashboardShell';
import { UserRole } from '@/lib/auth/roles';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import AnomalyItem from '@/components/dashboard/AnomalyItem';
import AnomalyDetailsModal from '@/components/dashboard/AnomalyDetailsModal';
import { AlertData, SeverityLevel, IncidentStatus } from '@/lib/types';
import { Filter, Search, CheckCircle } from 'lucide-react';

const AnomaliesPage = () => {
    const { alerts, isReady } = useRealtimeUpdates();
    const [selectedAnomaly, setSelectedAnomaly] = useState<AlertData | null>(null);
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [severityFilter, setSeverityFilter] = useState<SeverityLevel | 'ALL'>('ALL');
    const [statusFilter, setStatusFilter] = useState<IncidentStatus | 'ALL'>('ALL');
    const [searchQuery, setSearchQuery] = useState('');

    const filteredAlerts = useMemo(() => {
        return alerts
            .filter(alert => {
                const matchesSeverity = severityFilter === 'ALL' || alert.severity === severityFilter;
                const matchesStatus = statusFilter === 'ALL' || alert.status === statusFilter || (statusFilter === 'REPORTED' && !alert.status);
                const matchesSearch = alert.message.toLowerCase().includes(searchQuery.toLowerCase()) ||
                    (alert.description?.toLowerCase().includes(searchQuery.toLowerCase()));
                return matchesSeverity && matchesStatus && matchesSearch;
            })
            .slice()
            .reverse();
    }, [alerts, severityFilter, statusFilter, searchQuery]);

    const handleSelect = (alert: AlertData) => {
        setSelectedAnomaly(alert);
        setIsModalOpen(true);
    };

    const severityOptions: (SeverityLevel | 'ALL')[] = ['ALL', 'Critical', 'ERROR', 'Warning', 'Anomaly', 'INFO'];
    const statusOptions: (IncidentStatus | 'ALL')[] = ['ALL', 'REPORTED', 'ACKNOWLEDGED', 'RESOLVED'];

    return (
        <AuthGuard requiredRole={UserRole.AGENCY}>
            <DashboardShell>
                <div className="flex flex-col md:flex-row justify-between items-center mb-8 gap-4">
                    <h1 className="text-4xl font-bold uppercase tracking-tighter font-lcd matrix-glow text-lcd-text">Incident Reports</h1>
                    <div className="flex items-center gap-4 font-lcd">
                        <div className="flex items-center gap-2">
                            <span className="text-xs uppercase opacity-60">Total Active:</span>
                            <span className="text-2xl font-bold text-destructive">
                                {alerts.filter(a => a.status !== 'RESOLVED').length}
                            </span>
                        </div>
                        <div className="w-px h-8 bg-lcd-text/20"></div>
                        <div className="flex items-center gap-2">
                            <span className="text-xs uppercase opacity-60">Resolved:</span>
                            <span className="text-2xl font-bold text-matrix">
                                {alerts.filter(a => a.status === 'RESOLVED').length}
                            </span>
                        </div>
                    </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
                    {/* Filters Sidebar */}
                    <div className="md:col-span-1 space-y-6">
                        <div className="matrix-card p-4">
                            <h2 className="text-sm font-bold uppercase mb-4 flex items-center gap-2 font-lcd">
                                <Filter size={14} /> Global Filters
                            </h2>
                            <div className="space-y-4 font-lcd">
                                <div>
                                    <label className="text-[10px] uppercase opacity-60 block mb-1">Search</label>
                                    <div className="relative">
                                        <Search className="absolute left-2 top-1/2 -translate-y-1/2 opacity-40" size={14} />
                                        <input
                                            type="text"
                                            value={searchQuery}
                                            onChange={(e) => setSearchQuery(e.target.value)}
                                            placeholder="Keyword..."
                                            className="w-full bg-lcd-text/5 border border-lcd-text/20 rounded-none p-1.5 pl-8 text-xs focus:outline-none focus:border-lcd-text"
                                        />
                                    </div>
                                </div>
                                <div>
                                    <label className="text-[10px] uppercase opacity-60 block mb-1">Status</label>
                                    <div className="flex flex-wrap gap-1">
                                        {statusOptions.map(opt => (
                                            <button
                                                key={opt}
                                                onClick={() => setStatusFilter(opt)}
                                                className={`px-2 py-1 text-[10px] border border-lcd-text/20 transition-colors uppercase ${statusFilter === opt ? 'bg-lcd-text text-lcd-bg font-bold' : 'hover:bg-lcd-text/10'}`}
                                            >
                                                {opt}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                                <div>
                                    <label className="text-[10px] uppercase opacity-60 block mb-1">Severity</label>
                                    <div className="flex flex-wrap gap-1">
                                        {severityOptions.map(opt => (
                                            <button
                                                key={opt}
                                                onClick={() => setSeverityFilter(opt)}
                                                className={`px-2 py-1 text-[10px] border border-lcd-text/20 transition-colors uppercase ${severityFilter === opt ? 'bg-lcd-text text-lcd-bg font-bold' : 'hover:bg-lcd-text/10'}`}
                                            >
                                                {opt}
                                            </button>
                                        ))}
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div className="matrix-card p-4 opacity-70 font-lcd">
                            <h3 className="text-[10px] font-bold uppercase mb-2 flex items-center gap-1">
                                <CheckCircle size={12} className="text-matrix" /> Protocol
                            </h3>
                            <p className="text-[10px] leading-relaxed">
                                All incidents are state-tracked. Acknowledge urgent threats immediately.
                                Resolution requires identifying the root cause and documenting details for the audit log.
                            </p>
                        </div>
                    </div>

                    {/* Alerts List */}
                    <div className="md:col-span-3">
                        {!isReady ? (
                            <div className="py-20 text-center animate-pulse opacity-50 font-lcd">
                                <p className="tracking-widest uppercase font-bold">Synchronizing Uplink...</p>
                            </div>
                        ) : filteredAlerts.length === 0 ? (
                            <div className="py-20 text-center border-2 border-dashed border-lcd-text/20 rounded-none opacity-50 font-lcd">
                                <p className="tracking-widest uppercase text-sm">No Filtered Incidents Found</p>
                            </div>
                        ) : (
                            <div className="space-y-4">
                                {filteredAlerts.map((alert) => (
                                    <AnomalyItem
                                        key={alert.id || (alert.timestamp instanceof Date ? alert.timestamp.toISOString() : alert.timestamp) + Math.random()}
                                        {...alert}
                                        onSelect={() => handleSelect(alert)}
                                    />
                                ))}
                            </div>
                        )}
                    </div>
                </div>

                <AnomalyDetailsModal
                    anomaly={selectedAnomaly}
                    open={isModalOpen}
                    onOpenChange={setIsModalOpen}
                />
            </DashboardShell>
        </AuthGuard>
    );
};

export default AnomaliesPage;
