"use client";

import React, { useState, useEffect, useMemo } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { 
  AlertTriangle, 
  Activity, 
  Shield, 
  Search, 
  ChevronRight, 
  CheckCircle2, 
  XCircle,
  Eye,
  Info,
  Clock,
  MapPin,
  Video,
  Hash,
  ArrowRightLeft,
  ExternalLink
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { AlertData, IncidentStatus } from '@/lib/types';
import { incidentService } from '@/lib/services/incidentService';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';

interface IncidentCommandCenterProps {
  alerts: AlertData[];
  onIncidentUpdated?: (incidentId: string, status: IncidentStatus) => void;
  onJumpToFeed?: (feedId: string) => void;
}

const IncidentCommandCenter: React.FC<IncidentCommandCenterProps> = ({ alerts, onIncidentUpdated, onJumpToFeed }) => {
  const [selectedIncidentId, setSelectedIncidentId] = useState<string | number | null>(null);
  const [isUpdating, setIsUpdating] = useState(false);
  const [notes, setNotes] = useState('');
  const [viewMode, setViewMode] = useState<'diagnostic' | 'visual'>('visual');
  const [bootSequence, setBootSequence] = useState(true);

  useEffect(() => {
    const timer = setTimeout(() => setBootSequence(false), 1200);
    return () => clearTimeout(timer);
  }, []);

  const selectedIncident = useMemo(() => 
    alerts.find(a => a.id === selectedIncidentId) || null
  , [alerts, selectedIncidentId]);

  useEffect(() => {
    if (alerts.length > 0 && !selectedIncidentId) {
      setSelectedIncidentId(alerts[0].id || null);
    }
  }, [alerts, selectedIncidentId]);

  const handleUpdateStatus = async (status: IncidentStatus) => {
    if (!selectedIncidentId) return;
    setIsUpdating(true);
    
    let success = false;
    const idStr = String(selectedIncidentId);

    if (status === 'ACKNOWLEDGED') {
      success = await incidentService.acknowledgeIncident(idStr);
    } else if (status === 'RESOLVED') {
      success = await incidentService.resolveIncident(idStr, notes);
    } else if (status === 'FALSE_POSITIVE') {
      success = await incidentService.markFalsePositive(idStr, notes);
    }

    if (success) {
      if (onIncidentUpdated) onIncidentUpdated(idStr, status);
      setNotes('');
    }
    setIsUpdating(false);
  };

  const getSeverityColor = (severity: string) => {
    switch (severity.toUpperCase()) {
      case 'CRITICAL': return 'text-red-600 border-red-600 shadow-[0_0_10px_rgba(220,38,38,0.3)]';
      case 'WARNING': return 'text-yellow-600 border-yellow-600 shadow-[0_0_10px_rgba(202,138,4,0.3)]';
      case 'HIGH': return 'text-orange-600 border-orange-600 shadow-[0_0_10px_rgba(234,88,12,0.3)]';
      default: return 'text-blue-600 border-blue-600 shadow-[0_0_10px_rgba(37,99,235,0.3)]';
    }
  };

  const getStatusBadge = (status?: string) => {
    const s = status || 'REPORTED';
    switch (s) {
      case 'REPORTED': return <Badge variant="outline" className="border-lcd-text text-lcd-text">UNASSIGNED</Badge>;
      case 'ACKNOWLEDGED': return <Badge variant="default" className="bg-lcd-text text-lcd-bg">ACTIVE_INVESTIGATION</Badge>;
      case 'RESOLVED': return <Badge variant="outline" className="border-green-700 text-green-700">CLOSED_VALIDATED</Badge>;
      case 'FALSE_POSITIVE': return <Badge variant="outline" className="border-muted-foreground text-muted-foreground">CLOSED_INVALID</Badge>;
      default: return <Badge variant="outline">{s}</Badge>;
    }
  };

  return (
    <div className="flex flex-col h-[700px] border-2 border-lcd-text bg-black overflow-hidden relative font-lcd selection:bg-lcd-text selection:text-lcd-bg">
      {/* BOOT OVERLAY */}
      <AnimatePresence>
        {bootSequence && (
          <motion.div 
            initial={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="absolute inset-0 z-[100] bg-black flex flex-col items-center justify-center p-12"
          >
            <div className="w-full max-w-md space-y-4">
              <div className="flex justify-between text-[10px] font-mono text-green-500">
                <span>R1_BOOT_PROTOCOL_v2.1</span>
                <span className="animate-pulse">INITIALIZING...</span>
              </div>
              <div className="h-1 w-full bg-green-900 overflow-hidden">
                <motion.div 
                  initial={{ x: '-100%' }}
                  animate={{ x: '0%' }}
                  transition={{ duration: 1, ease: "linear" }}
                  className="h-full w-full bg-green-500"
                />
              </div>
              <div className="grid grid-cols-2 gap-x-12 gap-y-1 text-[8px] font-mono text-green-500/50 uppercase">
                <span>&gt; MAPPING_TOPOLOGY</span><span>OK</span>
                <span>&gt; NEURAL_LOAD_BALANCER</span><span>OK</span>
                <span>&gt; VISION_INF_POOL</span><span>WAIT</span>
                <span>&gt; ENCRYPTION_KEYS</span><span>READY</span>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* STATIC NOISE */}
      <div className="absolute inset-0 pointer-events-none z-40 opacity-[0.02] mix-blend-overlay bg-[url('https://grainy-gradients.vercel.app/noise.svg')]" />
      
      {/* SCANLINE OVERLAY */}
      <div className="absolute inset-0 pointer-events-none z-50 opacity-[0.05] bg-[repeating-linear-gradient(0deg,transparent,transparent_1px,rgba(0,255,65,0.1)_1px,rgba(0,255,65,0.1)_2px)]" />
      
      {/* HEADER */}
      <div className="bg-lcd-text text-lcd-bg p-3 flex items-center justify-between border-b-2 border-lcd-text">
        <div className="flex items-center gap-4">
          <div className="relative">
            <Activity className="h-5 w-5 animate-pulse" />
            <div className="absolute -top-1 -right-1 h-2 w-2 bg-red-600 rounded-full animate-ping" />
          </div>
          <div className="flex flex-col">
            <span className="text-sm font-bold tracking-[0.2em] leading-tight uppercase flex items-center gap-2">
              Incident Command Center
              <span className="bg-red-600 text-white text-[8px] px-1 animate-pulse">LIVE</span>
            </span>
            <span className="text-[8px] font-mono opacity-70 tracking-widest">PROTOCOL: R1_TACTICAL_OVERVIEW // NODE_ID: {Math.random().toString(16).slice(2, 8).toUpperCase()}</span>
          </div>
        </div>
        <div className="flex items-center gap-6">
          <div className="flex flex-col items-end">
            <span className="text-[10px] font-bold">SYSTEM STATUS</span>
            <span className="text-[10px] text-green-900 font-mono">NOMINAL_READY</span>
          </div>
          <Shield className="h-6 w-6" />
        </div>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* LEFT PANEL: INCIDENT QUEUE */}
        <div className="w-1/3 border-r-2 border-lcd-text flex flex-col bg-lcd-text/5">
          <div className="p-2 border-b border-lcd-text/20 bg-lcd-text/10 flex items-center justify-between">
            <span className="text-[10px] font-bold tracking-widest uppercase">Live Incident Queue</span>
            <span className="text-[10px] font-mono opacity-60">COUNT: {alerts.length}</span>
          </div>
          
          <ScrollArea className="flex-1">
            <div className="flex flex-col divide-y divide-lcd-text/10">
              <AnimatePresence mode="popLayout">
                {alerts.map((alert, index) => (
                  <motion.div
                    key={alert.id || index}
                    initial={{ x: -20, opacity: 0 }}
                    animate={{ x: 0, opacity: 1 }}
                    transition={{ delay: index * 0.05 }}
                    className={cn(
                      "p-3 cursor-pointer transition-all border-l-4 group relative overflow-hidden",
                      selectedIncidentId === alert.id 
                        ? "bg-lcd-text text-lcd-bg border-l-black" 
                        : "hover:bg-lcd-text/10 border-l-transparent"
                    )}
                    onClick={() => setSelectedIncidentId(alert.id || null)}
                  >
                    <div className="flex justify-between items-start mb-1">
                      <span className={cn(
                        "text-[10px] font-bold tracking-tighter",
                        selectedIncidentId === alert.id ? "text-lcd-bg" : "opacity-60"
                      )}>
                        #{String(alert.id).slice(0, 8).toUpperCase()}
                      </span>
                      <span className="text-[9px] font-mono opacity-50 uppercase">
                        {new Date(alert.timestamp).toLocaleTimeString()}
                      </span>
                    </div>
                    <div className="flex items-center gap-2 mb-1">
                      <AlertTriangle className={cn("h-3 w-3", alert.severity === 'Critical' ? 'text-red-600' : 'text-amber-600')} />
                      <span className="text-xs font-bold leading-none line-clamp-1 uppercase tracking-tight">{alert.message}</span>
                    </div>
                    <div className="flex items-center gap-2 mt-2">
                       <div className={cn(
                         "h-1 w-full bg-lcd-text/20",
                         selectedIncidentId === alert.id && "bg-black/20"
                       )}>
                         <div 
                           className={cn("h-full bg-current transition-all", getSeverityColor(alert.severity))}
                           style={{ width: alert.severity === 'Critical' ? '100%' : '60%' }} 
                         />
                       </div>
                    </div>
                  </motion.div>
                ))}
              </AnimatePresence>
            </div>
          </ScrollArea>
        </div>

        {/* RIGHT PANEL: INVESTIGATION CONSOLE */}
        <div className="flex-1 flex flex-col relative bg-black/20">
          {selectedIncident ? (
            <>
              {/* TOP ACTIONS BAR */}
              <div className="p-3 border-b border-lcd-text/20 flex items-center justify-between bg-lcd-text/5">
                <div className="flex items-center gap-4">
                  <div className="flex flex-col">
                    <span className="text-[10px] font-bold opacity-60">CURRENT_TARGET</span>
                    <span className="text-xs font-bold font-mono">INCIDENT_{String(selectedIncident.id).toUpperCase()}</span>
                  </div>
                  <div className="h-8 w-[1px] bg-lcd-text/20" />
                  {getStatusBadge(selectedIncident.status)}
                </div>
                <div className="flex gap-2">
                  <Button 
                    variant="outline" 
                    size="sm" 
                    className={cn(
                      "h-7 text-[10px] font-bold border-2 border-lcd-text",
                      viewMode === 'visual' ? "bg-lcd-text text-lcd-bg" : "bg-transparent text-lcd-text"
                    )}
                    onClick={() => setViewMode('visual')}
                  >
                    <Eye className="h-3 w-3 mr-1" /> VISUAL_LOG
                  </Button>
                  <Button 
                    variant="outline" 
                    size="sm" 
                    className={cn(
                      "h-7 text-[10px] font-bold border-2 border-lcd-text",
                      viewMode === 'diagnostic' ? "bg-lcd-text text-lcd-bg" : "bg-transparent text-lcd-text"
                    )}
                    onClick={() => setViewMode('diagnostic')}
                  >
                    <Hash className="h-3 w-3 mr-1" /> DIAGNOSTIC_DATA
                  </Button>
                </div>
              </div>

              {/* CONSOLE CONTENT */}
              <div className="flex-1 overflow-hidden flex flex-col p-4 gap-4">
                {viewMode === 'visual' ? (
                  <div className="flex-1 flex flex-col gap-4">
                    {/* SNAPSHOT AREA */}
                    <div className="flex-1 bg-black border-2 border-lcd-text/30 relative group overflow-hidden">
                      <div className="absolute top-2 left-2 z-10 flex flex-col gap-1">
                        <Badge className="bg-black/80 text-green-500 border border-green-500/50 text-[8px] font-mono">
                          CAM_FEED: {selectedIncident.feed_id || 'UNKNOWN'}
                        </Badge>
                        <Badge className="bg-black/80 text-green-500 border border-green-500/50 text-[8px] font-mono">
                          TIMESTAMP: {new Date(selectedIncident.timestamp).toISOString()}
                        </Badge>
                      </div>
                      
                      <div className="absolute inset-0 flex items-center justify-center pointer-events-none opacity-20">
                         <div className="w-full h-[1px] bg-lcd-text shadow-[0_0_10px_#000]" />
                         <div className="h-full w-[1px] bg-lcd-text shadow-[0_0_10px_#000]" />
                      </div>

                      {selectedIncident.details?.snapshot_path ? (
                        <img
                          src={`${process.env.NEXT_PUBLIC_API_BASE_URL || ''}/api/v1/snapshots/${selectedIncident.details.snapshot_path}`}
                          alt="Incident Snapshot"
                          className="w-full h-full object-contain filter grayscale contrast-125"
                        />
                      ) : (                        <div className="w-full h-full flex flex-col items-center justify-center gap-4 text-lcd-text/40">
                          <Video className="h-12 w-12 opacity-20" />
                          <span className="text-[10px] font-bold uppercase tracking-[0.3em]">No Visual Log Available</span>
                        </div>
                      )}
                      
                      {/* CORNER BRACKETS */}
                      <div className="absolute top-0 left-0 w-4 h-4 border-t-2 border-l-2 border-lcd-text" />
                      <div className="absolute top-0 right-0 w-4 h-4 border-t-2 border-r-2 border-lcd-text" />
                      <div className="absolute bottom-0 left-0 w-4 h-4 border-b-2 border-l-2 border-lcd-text" />
                      <div className="absolute bottom-0 right-0 w-4 h-4 border-b-2 border-r-2 border-lcd-text" />
                    </div>

                    {/* METADATA STRIP */}
                    <div className="grid grid-cols-3 gap-2">
                      <div className="bg-lcd-text/10 border border-lcd-text/20 p-2 flex flex-col">
                        <span className="text-[8px] font-bold opacity-50 uppercase">Location Reference</span>
                        <div className="flex items-center gap-2 mt-1">
                          <MapPin className="h-3 w-3" />
                          <span className="text-[10px] font-bold font-mono">LAT:{selectedIncident.latitude?.toFixed(4)}, LON:{selectedIncident.longitude?.toFixed(4)}</span>
                        </div>
                      </div>
                      <div className="bg-lcd-text/10 border border-lcd-text/20 p-2 flex flex-col">
                        <span className="text-[8px] font-bold opacity-50 uppercase">Temporal Reference</span>
                        <div className="flex items-center gap-2 mt-1">
                          <Clock className="h-3 w-3" />
                          <span className="text-[10px] font-bold font-mono">{new Date(selectedIncident.timestamp).toLocaleString()}</span>
                        </div>
                      </div>
                      <div className="bg-lcd-text/10 border border-lcd-text/20 p-2 flex flex-col">
                        <span className="text-[8px] font-bold opacity-50 uppercase">Analysis Engine</span>
                        <div className="flex items-center gap-2 mt-1">
                          <Search className="h-3 w-3" />
                          <span className="text-[10px] font-bold font-mono">VISION_CORE_V2.1</span>
                        </div>
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="flex-1 bg-black/40 border-2 border-lcd-text/20 p-6 font-mono text-xs overflow-auto">
                    <div className="flex flex-col gap-4">
                      <div className="flex flex-col gap-1 border-b border-lcd-text/20 pb-4">
                        <span className="text-lcd-text font-bold">--- INCIDENT_MANIFEST ---</span>
                        <span className="text-lcd-text/70">ID: {selectedIncident.id}</span>
                        <span className="text-lcd-text/70">TYPE: {selectedIncident.message}</span>
                        <span className="text-lcd-text/70">SEVERITY: {selectedIncident.severity}</span>
                      </div>
                      
                      <div className="flex flex-col gap-2">
                        <span className="text-lcd-text font-bold">--- EXTRACTED_METADATA ---</span>
                        {Object.entries(selectedIncident.details || {}).map(([key, value]) => (
                          <div key={key} className="flex justify-between border-b border-lcd-text/5 py-1">
                            <span className="opacity-50 uppercase">{key}</span>
                            <span className="text-right">{JSON.stringify(value)}</span>
                          </div>
                        ))}
                      </div>

                      <div className="mt-4 p-4 border border-amber-900/50 bg-amber-900/10 text-amber-500 animate-matrix-pulse">
                        <span className="font-bold uppercase">[!] SYSTEM_ADVISORY:</span>
                        <p className="mt-1 leading-relaxed opacity-80 uppercase tracking-tighter">
                          THIS DATA IS GENERATED BY AN AUTONOMOUS AGENT. MANUAL VALIDATION IS MANDATORY BEFORE COUNTERMEASURE DEPLOYMENT.
                        </p>
                      </div>
                    </div>
                  </div>
                )}

                {/* RESOLUTION AREA */}
                <div className="mt-auto border-t-2 border-lcd-text pt-4 bg-lcd-text/5 p-4 -m-4">
                  <div className="flex flex-col gap-3">
                    <div className="flex items-center justify-between">
                      <span className="text-[10px] font-bold tracking-widest uppercase">Operator Intervention</span>
                      <div className="flex gap-2">
                        {selectedIncident?.feed_id && (
                          <Button 
                            size="sm" 
                            className="bg-lcd-text/20 text-lcd-text hover:bg-lcd-text/40 transition-colors h-7 text-[10px] font-bold border-2 border-lcd-text/50"
                            onClick={() => {
                              if (selectedIncident.feed_id) {
                                onJumpToFeed?.(selectedIncident.feed_id);
                              }
                            }}
                          >
                            <ExternalLink className="h-3 w-3 mr-1" /> JUMP_TO_FEED
                          </Button>
                        )}
                        {selectedIncident?.status === 'REPORTED' && (
                          <Button 
                            size="sm" 
                            className="bg-lcd-text text-lcd-bg hover:bg-white transition-colors h-7 text-[10px] font-bold"
                            onClick={() => handleUpdateStatus('ACKNOWLEDGED')}
                            disabled={isUpdating}
                          >
                            <ChevronRight className="h-3 w-3 mr-1" /> ACKNOWLEDGE_SIGNAL
                          </Button>
                        )}
                      </div>
                    </div>
                    
                    <textarea 
                      className="w-full bg-black border-2 border-lcd-text/30 p-3 text-xs font-mono text-lcd-text focus:border-lcd-text/60 outline-none transition-all min-h-[80px]"
                      placeholder="ENTER OPERATOR OBSERVATIONS / RESOLUTION NOTES..."
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                    />
                    
                    <div className="flex gap-3">
                      <Button 
                        className="flex-1 bg-lcd-text text-lcd-bg hover:bg-white font-bold h-10 text-[10px] tracking-widest"
                        onClick={() => handleUpdateStatus('RESOLVED')}
                        disabled={isUpdating || !notes.trim()}
                      >
                        <CheckCircle2 className="h-4 w-4 mr-2" /> VALIDATE_AND_CLOSE
                      </Button>
                      <Button 
                        variant="outline"
                        className="flex-1 border-2 border-red-900/50 text-red-600 hover:bg-red-950 font-bold h-10 text-[10px] tracking-widest"
                        onClick={() => handleUpdateStatus('FALSE_POSITIVE')}
                        disabled={isUpdating || !notes.trim()}
                      >
                        <XCircle className="h-4 w-4 mr-2" /> MARK_AS_FALSE_POSITIVE
                      </Button>
                    </div>
                  </div>
                </div>
              </div>
            </>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center gap-6 opacity-30">
              <div className="relative">
                <Search className="h-16 w-12" />
                <motion.div 
                  animate={{ scale: [1, 1.2, 1], opacity: [0.5, 1, 0.5] }}
                  transition={{ duration: 2, repeat: Infinity }}
                  className="absolute inset-0 bg-lcd-text rounded-full blur-2xl -z-10" 
                />
              </div>
              <div className="flex flex-col items-center">
                <span className="text-xs font-bold uppercase tracking-[0.5em]">Awaiting Selection</span>
                <span className="text-[10px] font-mono mt-2">SELECT_INCIDENT_FROM_LEFT_MANIFEST</span>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* FOOTER BAR */}
      <div className="h-6 bg-lcd-text/10 border-t border-lcd-text/20 flex items-center px-3 justify-between">
        <div className="flex items-center gap-4 text-[8px] font-mono opacity-50 uppercase tracking-widest">
          <div className="flex items-center gap-1"><div className="h-1 w-1 rounded-full bg-green-500" /> SYNC_ESTABLISHED</div>
          <div className="flex items-center gap-1"><div className="h-1 w-1 rounded-full bg-green-500" /> SECURE_ENCRYPTION_AES256</div>
          <div>BUFFER_LOAD: {Math.floor(Math.random() * 10)}%</div>
        </div>
        <div className="text-[8px] font-mono opacity-50 uppercase">
          SESSION_UPTIME: 04:12:44
        </div>
      </div>
    </div>
  );
};

export default IncidentCommandCenter;
