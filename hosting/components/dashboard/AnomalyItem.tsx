"use client";

import React, { useState } from 'react';
import { cn } from '@/lib/utils';
import { AnomalyItemProps, AlertData, SeverityLevel } from '@/lib/types';
import { Bomb, XOctagon, AlertTriangle, Sigma, InfoIcon, ChevronRight, Activity, Check, CheckCircle, Trash2 } from 'lucide-react';
import { alertService } from '@/lib/services/alertService';

interface SeverityConfigEntry {
  styleClass: string;
  text: string;
  icon: React.ElementType;
}

const severityConfig: Record<SeverityLevel, SeverityConfigEntry> = {
  Critical: { styleClass: 'text-red-500 border-red-500 bg-red-500/10', text: 'CRITICAL', icon: Bomb },
  ERROR: { styleClass: 'text-red-500 border-red-500 bg-red-500/10', text: 'ERROR', icon: XOctagon },
  Warning: { styleClass: 'text-yellow-500 border-yellow-500 bg-yellow-500/10', text: 'WARNING', icon: AlertTriangle },
  Anomaly: { styleClass: 'text-lcd-green border-lcd-green bg-lcd-green/10', text: 'ANOMALY', icon: Sigma },
  INFO: { styleClass: 'text-blue-500 border-blue-500 bg-blue-500/10', text: 'INFO', icon: InfoIcon },
};

const AnomalyItem = React.memo((props: AnomalyItemProps & { numericId?: number; onDeleted?: () => void }) => {
  const { id, timestamp, severity, feed_id, message, status, onSelect, numericId, onDeleted, ...rest } = props;
  const [acked, setAcked] = useState(false);
  const [busy, setBusy] = useState(false);
  const config = severityConfig[severity] || severityConfig.Anomaly;
  const IconComponent = config.icon;
  const displayTime = new Date(timestamp).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

  const handleSelect = () => {
    if (onSelect) {
      const fullAlertData: AlertData = { id, timestamp, severity, feed_id, message, status, ...rest };
      onSelect(fullAlertData);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      handleSelect();
    }
  };

  // REST actions (backend /api/v1/alerts/{id}/acknowledge + DELETE, admin-only).
  // WS-persisted alerts carry string ids with no DB pk, so these buttons render
  // only when the page passes numericId (REST-sourced alerts). stopPropagation
  // keeps the row's onSelect from firing on an action click.
  const handleAck = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!numericId || busy) return;
    setBusy(true);
    const updated = await alertService.acknowledgeAlert(numericId, true);
    setBusy(false);
    if (updated) setAcked(true);
  };

  const handleDelete = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (!numericId || busy) return;
    setBusy(true);
    const ok = await alertService.deleteAlert(numericId);
    setBusy(false);
    if (ok) onDeleted?.();
  };

  const statusColors: Record<string, string> = {
    REPORTED: 'text-red-500 border-red-500/30',
    ACKNOWLEDGED: 'text-yellow-500 border-yellow-500/30',
    RESOLVED: 'text-emerald-500 border-emerald-500/30',
  };

  return (
    <div
      tabIndex={0}
      role="button"
      onClick={handleSelect}
      onKeyDown={handleKeyDown}
      className={cn(
        'group relative p-3 flex items-start gap-3 cursor-pointer border border-lcd-green/20 transition-all duration-200',
        'bg-industrial-panel hover:bg-lcd-green/10 hover:border-lcd-green',
        'hover:translate-x-1',
        status === 'RESOLVED' && 'opacity-50 grayscale-[0.5]'
      )}
    >
      {/* Severity Tape Indicator */}
      <div className={cn("absolute left-0 top-0 bottom-0 w-1", config.styleClass.split(' ')[0].replace('text-', 'bg-'))} />

      {/* Tactical Label */}
      <div className={cn(
          "flex items-center gap-1 px-1.5 py-0.5 border text-[9px] font-bold uppercase tracking-tighter font-lcd",
          config.styleClass
      )}>
        <IconComponent className="h-3 w-3" />
        {config.text}
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between gap-2 mb-1">
          <p className="text-sm font-medium truncate tracking-wide font-lcd group-hover:text-lcd-green transition-colors" title={message}>
            {message}
          </p>
          {status && (
            <span className={cn(
                "text-[8px] px-1 border font-mono uppercase transition-colors", 
                statusColors[status] || statusColors.REPORTED
            )}>
              {status}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 text-[10px] opacity-50 group-hover:opacity-90 mt-1 tracking-widest font-lcd">
          <span className="flex items-center gap-1">
            <Activity className="h-2 w-2" />
            {displayTime}
          </span>
          {feed_id && (
            <span className="flex items-center gap-1">
              <span className="opacity-40">FEED:</span>
              <span>{feed_id}</span>
            </span>
          )}
        </div>
      </div>

      {/* REST actions: ack + delete (numericId present = REST-sourced alert) */}
      {numericId !== undefined && (
        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity self-center">
          <button
            onClick={handleAck}
            disabled={busy || acked}
            title={acked ? 'Acknowledged' : 'Acknowledge alert (admin)'}
            className={cn(
              'p-1.5 border transition-all hover:scale-105 active:scale-95 font-lcd',
              acked
                ? 'border-emerald-500 text-emerald-500 cursor-default'
                : 'border-lcd-text/30 text-lcd-text/70 hover:border-emerald-500 hover:text-emerald-500'
            )}
          >
            {acked ? <Check size={12} /> : <CheckCircle size={12} />}
          </button>
          <button
            onClick={handleDelete}
            disabled={busy}
            title="Delete alert (admin)"
            className="p-1.5 border border-lcd-text/30 text-lcd-text/70 hover:border-red-500 hover:text-red-500 transition-all hover:scale-105 active:scale-95"
          >
            <Trash2 size={12} />
          </button>
        </div>
      )}

      <ChevronRight className="h-4 w-4 opacity-30 group-hover:opacity-100 transition-opacity self-center text-lcd-green" />
    </div>
  );
});

AnomalyItem.displayName = 'AnomalyItem';
export default AnomalyItem;
