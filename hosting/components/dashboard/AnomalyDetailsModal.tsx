import React from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { AnomalyDetailsModalProps, SeverityLevel } from '@/lib/types';
import { incidentService } from '@/lib/services/incidentService';
import {
  AlertTriangle,
  Bomb,
  XOctagon,
  Sigma,
  InfoIcon,
  Calendar,
  Clock,
  MessageSquare,
  MapPin,
  Video,
} from 'lucide-react';

const severityConfig: Record<SeverityLevel, { color: string; text: string; icon: React.ElementType }> = {
  Critical: { color: 'bg-destructive text-destructive-foreground', text: 'Critical', icon: Bomb },
  ERROR: { color: 'bg-destructive text-destructive-foreground', text: 'Error', icon: XOctagon },
  Warning: { color: 'bg-yellow-500 text-white', text: 'Warning', icon: AlertTriangle },
  Anomaly: { color: 'bg-blue-500 text-white', text: 'Anomaly', icon: Sigma },
  INFO: { color: 'bg-blue-400 text-white', text: 'Info', icon: InfoIcon },
};

const AnomalyDetailsModal = ({ anomaly, open, onOpenChange, onAcknowledge }: AnomalyDetailsModalProps) => {
  const [isUpdating, setIsUpdating] = React.useState(false);
  const [notes, setNotes] = React.useState('');
  const [showResolveInput, setShowResolveInput] = React.useState(false);

  if (!anomaly) {
    return null;
  }

  const config = severityConfig[anomaly.severity] || severityConfig.Anomaly;
  const SeverityIcon = config.icon;

  const dateObj = new Date(anomaly.timestamp);
  const formattedDate = dateObj.toLocaleDateString(undefined, {
    weekday: 'long',
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });
  const formattedTime = dateObj.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });

  const handleAcknowledge = async () => {
    if (!anomaly.id) return;
    setIsUpdating(true);
    const success = await incidentService.acknowledgeIncident(String(anomaly.id));
    if (success) {
      if (onAcknowledge) onAcknowledge(anomaly);
      // We don't close the modal yet, let WebSocket update the state or we close it here
      onOpenChange(false);
    }
    setIsUpdating(false);
  };

  const handleUpdateStatus = async (status: 'RESOLVED' | 'FALSE_POSITIVE') => {
    if (!anomaly.id) return;
    setIsUpdating(true);
    let success = false;
    if (status === 'RESOLVED') {
      success = await incidentService.resolveIncident(String(anomaly.id), notes);
    } else {
      success = await incidentService.markFalsePositive(String(anomaly.id), notes);
    }
    
    if (success) {
      onOpenChange(false);
      setShowResolveInput(false);
      setNotes('');
    }
    setIsUpdating(false);
  };

  return (
    <Dialog open={open} onOpenChange={(val) => {
      onOpenChange(val);
      if (!val) {
        setShowResolveInput(false);
        setNotes('');
      }
    }}>
      <DialogContent className="sm:max-w-[500px] bg-card border-border text-foreground p-6">
        <DialogHeader className="mb-4 text-left">
          <DialogTitle className="flex items-center gap-2 text-lg font-semibold" id="anomaly-dialog-title">
            <SeverityIcon className={cn("h-5 w-5", config.color.includes('yellow') || config.color.includes('amber') ? 'text-black' : 'text-white')} />
            Incident Details
          </DialogTitle>
          <DialogDescription id="anomaly-dialog-desc" className="text-muted-foreground pt-1">
            {anomaly.id ? `ID: ${anomaly.id}` : 'Anonymous Event'}
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 py-4 text-sm border-t border-b border-border">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-3">
              <Badge variant="default" className={cn(config.color, 'h-6 px-2 font-semibold flex-shrink-0')}>
                {config.text}
              </Badge>
              <span className="font-bold text-foreground leading-snug">{anomaly.message}</span>
            </div>
            {anomaly.status && (
              <Badge variant="outline" className="font-mono text-[10px] h-5">
                {anomaly.status}
              </Badge>
            )}
          </div>

          <div className="grid grid-cols-[auto_1fr] items-start gap-x-3 gap-y-3 text-muted-foreground pl-2">
            <Calendar className="h-4 w-4 mt-0.5" />
            <span>{formattedDate}</span>

            <Clock className="h-4 w-4 mt-0.5" />
            <span>{formattedTime}</span>

            {anomaly.description && (
              <>
                <MessageSquare className="h-4 w-4 mt-0.5" />
                <span className="text-foreground italic">{anomaly.description}</span>
              </>
            )}

            {anomaly.location && (
              <>
                <MapPin className="h-4 w-4 mt-0.5" />
                <span>{anomaly.location}</span>
              </>
            )}

            {anomaly.feed_id && (
              <>
                <Video className="h-4 w-4 mt-0.5" />
                <span>Feed: {anomaly.feed_id}</span>
              </>
            )}
          </div>

          {anomaly.details?.snapshot_path && (
            <div className="mt-4 border-2 border-lcd-text/20 bg-black overflow-hidden">
              <div className="bg-lcd-text text-lcd-bg px-2 py-0.5 text-[8px] font-bold uppercase">
                Incident Snapshot // High-Res Capture
              </div>
              <img 
                src={`${process.env.NEXT_PUBLIC_API_URL || ''}/api/v1/snapshots/${anomaly.details.snapshot_path}`} 
                alt="Incident Snapshot"
                className="w-full h-auto object-contain max-h-[300px]"
                onError={(e) => {
                  (e.target as HTMLImageElement).style.display = 'none';
                }}
              />
            </div>
          )}

          {showResolveInput && (
            <div className="mt-2 space-y-2">
              <label className="text-[10px] uppercase font-bold text-muted-foreground">Resolution Notes</label>
              <textarea
                className="w-full bg-background border border-border p-2 rounded text-xs min-h-[60px]"
                placeholder="Enter resolution details..."
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
              />
              <div className="flex gap-2 pt-2">
                <Button 
                  className="flex-1 bg-matrix text-matrix-foreground hover:bg-matrix/90 font-bold text-xs"
                  onClick={() => handleUpdateStatus('RESOLVED')}
                  disabled={isUpdating}
                >
                  RESOLVE
                </Button>
                <Button 
                  variant="destructive"
                  className="flex-1 font-bold text-xs"
                  onClick={() => handleUpdateStatus('FALSE_POSITIVE')}
                  disabled={isUpdating}
                >
                  FALSE POSITIVE
                </Button>
              </div>
            </div>
          )}
        </div>

        <DialogFooter className="mt-6 gap-2 sm:justify-end">
          <DialogClose asChild>
            <Button type="button" variant="secondary" disabled={isUpdating}>Close</Button>
          </DialogClose>

          {!showResolveInput && anomaly.status !== 'RESOLVED' && (
            <>
              {anomaly.status === 'REPORTED' && (
                <Button
                  type="button"
                  variant="outline"
                  onClick={handleAcknowledge}
                  disabled={isUpdating}
                >
                  Acknowledge
                </Button>
              )}
              <Button
                type="button"
                className="bg-matrix text-matrix-foreground hover:bg-matrix/90"
                onClick={() => setShowResolveInput(true)}
                disabled={isUpdating}
              >
                Resolve
              </Button>
            </>
          )}

          {showResolveInput && (
            <Button
              type="button"
              className="bg-matrix text-matrix-foreground hover:bg-matrix/90"
              onClick={handleResolve}
              disabled={isUpdating || !notes.trim()}
            >
              Submit Resolution
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default AnomalyDetailsModal;