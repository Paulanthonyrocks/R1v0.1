"use client";

import React, { useState, useEffect, useMemo } from "react";
import AuthGuard from "@/components/auth/AuthGuard";
import DashboardShell from "@/components/dashboard/DashboardShell";
import { UserRole } from "@/lib/auth/roles";
import { Search, Terminal, ShieldAlert, Info, X } from 'lucide-react';
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { APIClient } from '@/lib/api/APIClient';

interface LogEntry {
    id: number;
    title: string;
    description: string;
    timestamp: string;
    type: string;
    severity: 'High' | 'Medium' | 'Low';
    source: string;
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "";

const SystemLogsPage = () => {
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [selectedLog, setSelectedLog] = useState<LogEntry | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [severityFilter, setSeverityFilter] = useState<'ALL' | 'High' | 'Medium' | 'Low'>('ALL');

  useEffect(() => {
    const fetchLogs = async () => {
        setLoading(true);
        try {
            const apiClient = APIClient.getInstance({ baseURL: API_BASE_URL });
            // Assuming /api/v1/logs endpoint exists based on backend routers
            const data = await apiClient.get<LogEntry[]>('/api/v1/logs');
            setLogs(data);
        } catch (error) {
            console.error("Failed to fetch logs:", error);
        } finally {
            setLoading(false);
        }
    };

    fetchLogs();
  }, []);

  const filteredLogs = useMemo(() => {
      return logs.filter(log => {
          const matchesSeverity = severityFilter === 'ALL' || log.severity === severityFilter;
          const matchesSearch = log.title.toLowerCase().includes(searchQuery.toLowerCase()) || 
                               log.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
                               log.source.toLowerCase().includes(searchQuery.toLowerCase());
          return matchesSeverity && matchesSearch;
      });
  }, [logs, severityFilter, searchQuery]);

  if (loading) return (
      <div className="bg-lcd-bg text-lcd-text h-screen flex flex-col items-center justify-center font-lcd">
          <Terminal className="animate-pulse mb-4" size={48} />
          <h1 className="text-2xl font-bold uppercase tracking-[0.3em]">Accessing System Logs...</h1>
      </div>
  );

  return (
    <AuthGuard requiredRole={UserRole.ADMIN}>
      <DashboardShell>
          <div className="flex flex-col lg:flex-row justify-between items-end mb-8 gap-6">
              <div className="flex-1">
                  <h1 className="text-4xl font-bold uppercase tracking-tighter font-lcd matrix-glow text-lcd-text mb-2">System Telemetry Logs</h1>
                  <p className="text-lcd-text/60 max-w-2xl text-sm font-lcd">
                      Low-level audit records from the traffic management engine. 
                      Monitoring real-time events across the distributed processing cluster.
                  </p>
              </div>
              
              <div className="flex flex-col sm:flex-row gap-4 w-full lg:w-auto">
                  <div className="relative flex-1 sm:w-80 font-bold">
                      <Search className="absolute left-3 top-1/2 -translate-y-1/2 opacity-40" size={18} />
                      <input
                        type="text"
                        placeholder="FILTER LOGS..."
                        className="bg-lcd-text/5 border-2 border-lcd-text/20 text-lcd-text rounded-none p-3 pl-10 w-full focus:outline-none focus:border-lcd-text tracking-[0.1em] placeholder:text-lcd-text/30 font-lcd"
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                      />
                  </div>
                  <div className="flex bg-lcd-text/10 p-1 border border-lcd-text/20">
                      {(['ALL', 'High', 'Medium', 'Low'] as const).map((s) => (
                          <button
                            key={s}
                            onClick={() => setSeverityFilter(s)}
                            className={`px-4 py-1 text-[10px] uppercase transition-all font-bold font-lcd ${severityFilter === s ? 'bg-lcd-text text-lcd-bg' : 'hover:bg-lcd-text/20'}`}
                          >
                              {s}
                          </button>
                      ))}
                  </div>
              </div>
          </div>

          {/* Log List */}
          <div className="matrix-card overflow-hidden p-0">
              <div className="overflow-x-auto">
                  <table className="w-full text-sm font-lcd">
                      <thead>
                          <tr className="bg-lcd-text/10 text-lcd-text border-b-2 border-lcd-text/20 uppercase text-[10px] font-bold tracking-widest">
                              <th className="p-4 text-left">Level</th>
                              <th className="p-4 text-left">Timestamp</th>
                              <th className="p-4 text-left">Event</th>
                              <th className="p-4 text-left">Source</th>
                              <th className="p-4 text-right">Actions</th>
                          </tr>
                      </thead>
                      <tbody>
                          {filteredLogs.map((log) => (
                              <tr key={log.id} className="border-b border-lcd-text/5 hover:bg-lcd-text/5 transition-colors group cursor-pointer" onClick={() => setSelectedLog(log)}>
                                  <td className="p-4 whitespace-nowrap">
                                      <span className={cn(
                                          "px-2 py-0.5 text-[10px] font-bold uppercase border border-lcd-text",
                                          log.severity === 'High' ? "bg-red-600 text-white" :
                                          log.severity === 'Medium' ? "bg-yellow-600 text-black" :
                                          "bg-green-600 text-white"
                                      )}>
                                          {log.severity}
                                      </span>
                                  </td>
                                  <td className="p-4 whitespace-nowrap opacity-60 text-xs">
                                      {new Date(log.timestamp).toLocaleString()}
                                  </td>
                                  <td className="p-4">
                                      <div className="font-bold uppercase tracking-tight">{log.title}</div>
                                      <div className="text-[10px] opacity-50 truncate max-w-md">{log.description}</div>
                                  </td>
                                  <td className="p-4 whitespace-nowrap">
                                      <span className="text-[10px] bg-lcd-text/10 px-2 py-1 border border-lcd-text/20 uppercase font-bold">{log.source}</span>
                                  </td>
                                  <td className="p-4 text-right">
                                      <Button variant="ghost" size="sm" className="h-8 w-8 p-0 hover:bg-lcd-text hover:text-lcd-bg">
                                          <Info size={14} />
                                      </Button>
                                  </td>
                              </tr>
                          ))}
                      </tbody>
                  </table>
              </div>
          </div>

        {/* Detail Modal Overlay */}
        {selectedLog && (
            <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
                <div className="absolute inset-0 bg-black/80 backdrop-blur-sm" onClick={() => setSelectedLog(null)}></div>
                <div className="relative matrix-card max-w-2xl w-full p-8 shadow-2xl animate-in zoom-in-95 duration-200">
                    <button 
                        onClick={() => setSelectedLog(null)}
                        className="absolute top-4 right-4 text-lcd-text/40 hover:text-lcd-text transition-colors"
                    >
                        <X size={24} />
                    </button>
                    
                    <div className="flex items-center gap-4 mb-6 pb-4 border-b border-lcd-text/20">
                        <Terminal size={32} className="text-lcd-text" />
                        <div>
                            <h2 className="text-2xl font-bold uppercase tracking-tighter font-lcd">{selectedLog.title}</h2>
                            <p className="text-[10px] uppercase opacity-60 font-lcd">Log ID: 0x{selectedLog.id.toString(16).padStart(4, '0')} | Source: {selectedLog.source}</p>
                        </div>
                    </div>

                    <div className="space-y-6 font-lcd">
                        <div className="grid grid-cols-2 gap-8">
                            <div>
                                <label className="text-[10px] uppercase font-bold opacity-40 block mb-1">Severity</label>
                                <div className="text-xl font-bold uppercase">{selectedLog.severity} Level</div>
                            </div>
                            <div>
                                <label className="text-[10px] uppercase font-bold opacity-40 block mb-1">Classification</label>
                                <div className="text-xl font-bold uppercase">{selectedLog.type}</div>
                            </div>
                        </div>

                        <div>
                            <label className="text-[10px] uppercase font-bold opacity-40 block mb-1">Detailed Payload</label>
                            <div className="bg-lcd-text p-4 border border-lcd-text/20 font-mono text-sm leading-relaxed text-lcd-bg">
                                {selectedLog.description}
                            </div>
                        </div>

                        <div className="pt-4 flex justify-between items-center opacity-40">
                            <div className="text-[10px] uppercase">Kernel Timestamp: {selectedLog.timestamp}</div>
                            <ShieldAlert size={16} />
                        </div>
                    </div>
                </div>
            </div>
        )}
      </DashboardShell>
    </AuthGuard>
  );
};

export default SystemLogsPage;