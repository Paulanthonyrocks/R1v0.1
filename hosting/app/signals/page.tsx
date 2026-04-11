"use client";

import React, { useState, useEffect } from 'react';
import DashboardShell from '@/components/dashboard/DashboardShell';
import AuthGuard from '@/components/auth/AuthGuard';
import { UserRole } from '@/lib/auth/roles';
import { Signal as SignalIcon, Loader2, Save, AlertCircle, Info } from 'lucide-react';
import { cn } from '@/lib/utils';
import { useAuth } from '@/lib/auth/AuthProvider';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Label } from '@/components/ui/label';

interface Signal {
  id: string;
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "";

const SignalsPage = () => {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [selectedSignal, setSelectedSignal] = useState<string>("");
  const [phase, setPhase] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [fetching, setFetching] = useState(true);
  const { token } = useAuth();

  useEffect(() => {
    const fetchSignals = async () => {
      if (!token) return;
      
      setFetching(true);
      try {
        const response = await fetch(`${API_BASE_URL}/api/v1/signals`, {
          headers: {
            'Authorization': `Bearer ${token}`,
            'Bypass-Tunnel-Reminder': 'true'
          },
        });
        if (response.ok) {
            const data = await response.json();
            setSignals(data);
        }
      } catch (error) {
        console.error('Error fetching signals:', error);
      } finally {
        setFetching(false);
      }
    };

    fetchSignals();
  }, [token]);

  const updateSignalPhase = async () => {
    if (!selectedSignal || !phase || !token) return;

    setLoading(true);
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/signals/${selectedSignal}/set_phase`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
          'Bypass-Tunnel-Reminder': 'true'
        },
        body: JSON.stringify({ phase }),
      });

      if (response.ok) {
        alert('Signal phase updated successfully');
      } else {
        const errData = await response.json();
        alert(`Failed to update signal phase: ${errData.detail || 'Unknown error'}`);
      }
    } catch (error) {
      console.error('Error updating signal phase:', error);
      alert('Network error while updating signal phase');
    } finally {
      setLoading(false);
    }
  };

      return (
      <AuthGuard requiredRole={UserRole.AGENCY}>
        <DashboardShell>
            <div className="retro-title-container">
                <div className="flex flex-col md:flex-row justify-between items-end gap-4">
                    <div>
                        <h1 className="text-5xl font-black uppercase tracking-tighter font-lcd matrix-glow text-lcd-text mb-1">Signal Control</h1>
                        <div className="flex items-center gap-2">
                            <span className="terminal-text text-[10px]">REMOTE.COMMAND.INTERFACE // INTERSECTION_OVERRIDE_v2.4</span>
                        </div>
                    </div>
                    <div className="flex bg-lcd-text/5 px-4 py-2 border-2 border-lcd-text font-bold text-[10px] uppercase tracking-widest items-center gap-4">
                        <div className="flex items-center gap-2">
                            <div className="h-2 w-2 rounded-full bg-green-600 animate-pulse" />
                            Safety Interlock: Active
                        </div>
                    </div>
                </div>
            </div>
  
            <div className="grid grid-cols-1 lg:grid-cols-4 gap-12">
                <Card className="lg:col-span-2 matrix-card p-0 overflow-hidden">
                    <div className="matrix-card-header bg-lcd-text/10">
                        <div className="flex items-center gap-2">
                            <SignalIcon size={14} />
                            <span>Manual Phase Override // Hardware Uplink</span>
                        </div>
                    </div>
                    <div className="p-8 space-y-8 bg-lcd-text/5">
                        {fetching ? (
                            <div className="flex flex-col items-center justify-center py-20 gap-4">
                                <Loader2 className="animate-spin text-lcd-text h-10 w-10 opacity-30" />
                                <p className="text-[10px] font-black uppercase tracking-[0.2em]">Scanning Local Topology...</p>
                            </div>
                        ) : signals.length === 0 ? (
                            <div className="text-center py-20 border-4 border-dashed border-lcd-text/10 rounded font-black opacity-30 text-xl uppercase tracking-widest">
                                No Controllers Detected on Network
                            </div>
                        ) : (
                            <>
                              <div className="space-y-3">
                                  <Label htmlFor="signal-select" className="text-[10px] uppercase font-black tracking-[0.1em] opacity-60">Controller Address (UUID)</Label>
                                  <Select value={selectedSignal} onValueChange={setSelectedSignal}>
                                      <SelectTrigger id="signal-select" className="bg-black/10 border-2 border-lcd-text/30 font-black h-14 text-xl uppercase tracking-tighter transition-all focus:border-lcd-text">
                                          <SelectValue placeholder="SELECT_HARDWARE_TARGET..." />
                                      </SelectTrigger>
                                      <SelectContent className="bg-lcd-bg border-4 border-lcd-text text-lcd-text font-black uppercase">
                                          {signals.map((s) => (
                                              <SelectItem key={s.id} value={s.id} className="hover:bg-lcd-text hover:text-lcd-bg py-3">{s.id}</SelectItem>
                                          ))}
                                      </SelectContent>
                                  </Select>
                              </div>
  
                              <div className="space-y-3">
                                  <Label htmlFor="phase-select" className="text-[10px] uppercase font-black tracking-[0.1em] opacity-60">Commanded Operational Phase</Label>
                                  <div className="grid grid-cols-3 gap-4">
                                      {['green', 'yellow', 'red'].map((p) => (
                                          <button
                                              key={p}
                                              onClick={() => setPhase(p)}
                                              className={cn(
                                                  "h-24 border-4 transition-all flex flex-col items-center justify-center gap-2 group",
                                                  phase === p 
                                                      ? p === 'green' ? "bg-green-600 text-white border-green-800 scale-105 shadow-lg" :
                                                        p === 'yellow' ? "bg-yellow-400 text-black border-yellow-600 scale-105 shadow-lg" :
                                                        "bg-red-600 text-white border-red-800 scale-105 shadow-lg"
                                                      : "bg-lcd-text/5 border-lcd-text/20 grayscale opacity-40 hover:opacity-100"
                                              )}
                                          >
                                              <div className={cn(
                                                  "w-4 h-4 rounded-full border-2 border-lcd-text",
                                                  p === 'green' ? "bg-green-500" : p === 'yellow' ? "bg-yellow-400" : "bg-red-500",
                                                  phase === p && "animate-pulse"
                                              )} />
                                              <span className="text-[10px] font-black uppercase tracking-widest">{p}</span>
                                          </button>
                                      ))}
                                  </div>
                              </div>
  
                              <Button 
                                  onClick={updateSignalPhase} 
                                  disabled={!selectedSignal || !phase || loading}
                                  className="w-full matrix-btn-sleek h-16 text-xl tracking-widest bg-lcd-text text-lcd-bg"
                              >
                                  {loading ? <Loader2 className="animate-spin mr-3 h-6 w-6" /> : <Save className="mr-3 h-6 w-6" />}
                                  EXECUTE_COMMAND
                              </Button>
                            </>
                        )}
                    </div>
                </Card>
  
                <div className="lg:col-span-2 space-y-8">
                    <div className="matrix-card p-0 overflow-hidden">
                        <div className="matrix-card-header">
                            <span>Diagnostic Logs</span>
                        </div>
                        <div className="p-8 space-y-6 bg-lcd-text/[0.02]">
                            <div className="flex items-center justify-between border-b-2 border-lcd-text/10 pb-4">
                                <span className="text-[10px] font-black uppercase opacity-40">Session Identity</span>
                                <span className="text-xs font-bold uppercase">AUTH.ADMIN.ROOT</span>
                            </div>
                            <div className="flex items-center justify-between border-b-2 border-lcd-text/10 pb-4">
                                <span className="text-[10px] font-black uppercase opacity-40">Uplink Encryption</span>
                                <span className="text-xs font-bold uppercase text-green-700">AES-256-GCM_ACTIVE</span>
                            </div>
                            <div className="flex items-center justify-between border-b-2 border-lcd-text/10 pb-4">
                                <span className="text-[10px] font-black uppercase opacity-40">Command Latency</span>
                                <span className="text-xs font-bold uppercase">42ms</span>
                            </div>
                            
                            <div className="pt-4">
                                <h3 className="text-[10px] font-black uppercase tracking-widest opacity-40 mb-3">Operational Directives</h3>
                                <div className="space-y-4">
                                    <div className="p-4 bg-yellow-500/10 border-2 border-yellow-600/30 flex gap-4">
                                        <AlertCircle className="text-yellow-600 shrink-0" size={20} />
                                        <p className="text-[10px] font-bold uppercase leading-relaxed text-yellow-800">
                                            Manual override requests are logged for administrative review. 
                                            Ensure all transitions adhere to safety timing parameters.
                                        </p>
                                    </div>
                                    <div className="p-4 bg-lcd-text/5 border-2 border-lcd-text/10 flex gap-4">
                                        <Info className="text-lcd-text/50 shrink-0" size={20} />
                                        <p className="text-[10px] font-bold uppercase leading-relaxed opacity-60">
                                            System interlocks prevent "Green-Green" conflict states. 
                                            All commands are validated against local controller logic.
                                        </p>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </DashboardShell>
      </AuthGuard>
    );
  };
  
export default SignalsPage;