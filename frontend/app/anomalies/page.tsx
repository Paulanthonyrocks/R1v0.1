"use client";

import React, { useState, useMemo } from 'react';
import AuthGuard from '@/components/auth/AuthGuard';
import DashboardShell from '@/components/dashboard/DashboardShell';
import { UserRole } from '@/lib/auth/roles';
import { useRealtimeUpdates } from '@/lib/hook/useRealtimeUpdates';
import AnomalyItem from '@/components/dashboard/AnomalyItem';
import AnomalyDetailsModal from '@/components/dashboard/AnomalyDetailsModal';
import { AlertData, SeverityLevel } from '@/lib/types';
import { Filter, Search } from 'lucide-react';

const AnomaliesPage = () => {
  const { alerts, isReady } = useRealtimeUpdates();
  const [selectedAnomaly, setSelectedAnomaly] = useState<AlertData | null>(null);
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [severityFilter, setSeverityLevel] = useState<SeverityLevel | 'ALL'>('ALL');
  const [searchQuery, setSearchQuery] = useState('');

  const filteredAlerts = useMemo(() => {
    return alerts
      .filter(alert => {
        const matchesSeverity = severityFilter === 'ALL' || alert.severity === severityFilter;
        const matchesSearch = alert.message.toLowerCase().includes(searchQuery.toLowerCase()) || 
                             (alert.description?.toLowerCase().includes(searchQuery.toLowerCase()));
        return matchesSeverity && matchesSearch;
      })
      .slice()
      .reverse();
  }, [alerts, severityFilter, searchQuery]);

  const handleSelect = (alert: AlertData) => {
    setSelectedAnomaly(alert);
    setIsModalOpen(true);
  };

  const severityOptions: (SeverityLevel | 'ALL')[] = ['ALL', 'Critical', 'ERROR', 'Warning', 'Anomaly', 'INFO'];

  return (
    <AuthGuard requiredRole={UserRole.AGENCY}>
      <DashboardShell>
          <div className="flex flex-col md:flex-row justify-between items-center mb-8 gap-4">
              <h1 className="text-4xl font-bold uppercase tracking-tighter">System Anomalies</h1>
              <div className="flex items-center gap-2">
                  <span className="text-xs uppercase opacity-60">Total Active:</span>
                  <span className="text-2xl font-bold">{alerts.length}</span>
              </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-6 mb-8">
              {/* Filters Sidebar */}
              <div className="md:col-span-1 space-y-6">
                  <div className="matrix-card p-4">
                      <h2 className="text-sm font-bold uppercase mb-4 flex items-center gap-2">
                          <Filter size={14} /> Filters
                      </h2>
                      <div className="space-y-4">
                          <div>
                              <label className="text-[10px] uppercase opacity-60 block mb-1">Search</label>
                              <div className="relative">
                                  <Search className="absolute left-2 top-1/2 -translate-y-1/2 opacity-40" size={14} />
                                  <input 
                                    type="text" 
                                    value={searchQuery}
                                    onChange={(e) => setSearchQuery(e.target.value)}
                                    placeholder="Keyword..."
                                    className="w-full bg-lcd-text/5 border border-lcd-text/20 rounded p-1.5 pl-8 text-xs focus:outline-none focus:border-primary"
                                  />
                              </div>
                          </div>
                          <div>
                              <label className="text-[10px] uppercase opacity-60 block mb-1">Severity</label>
                              <div className="flex flex-col gap-1">
                                  {severityOptions.map(opt => (
                                      <button
                                        key={opt}
                                        onClick={() => setSeverityLevel(opt)}
                                        className={`text-left px-2 py-1 text-xs rounded transition-colors ${severityFilter === opt ? 'bg-lcd-text text-lcd-bg font-bold' : 'hover:bg-lcd-text/10'}`}
                                      >
                                          {opt}
                                      </button>
                                  ))}
                              </div>
                          </div>
                      </div>
                  </div>

                  <div className="matrix-card p-4 opacity-60">
                      <h3 className="text-[10px] font-bold uppercase mb-2">Notice</h3>
                      <p className="text-[10px] leading-relaxed">
                          Anomalies are detected automatically via high-frequency telemetry analysis. 
                          Historical records are purged every 24 hours.
                      </p>
                  </div>
              </div>

              {/* Alerts List */}
              <div className="md:col-span-3">
                  {!isReady ? (
                      <div className="py-20 text-center animate-pulse opacity-50">
                          <p className="tracking-widest uppercase font-bold">Establishing Secure Uplink...</p>
                      </div>
                  ) : filteredAlerts.length === 0 ? (
                      <div className="py-20 text-center border-2 border-dashed border-lcd-text/20 rounded opacity-50">
                          <p className="tracking-widest uppercase text-sm">No Matching Anomalies Detected</p>
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