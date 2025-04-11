// components/dashboard/AnomalyDetailsModal.tsx
import React from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { AlertTriangle, Calendar, Clock, MessageSquare, MapPin, Video } from 'lucide-react';
import { cn } from "@/lib/utils";
import { AnomalyDetailsModalProps, SeverityLevel, AlertData } from '@/lib/types'; // Import AlertData

// Severity config (can be moved to shared utils if needed elsewhere)
const severityConfig: Record<SeverityLevel, { color: string; text: string; icon: React.ElementType }> = {
  Critical: { color: 'bg-destructive text-destructive-foreground', text: 'Critical', icon: AlertTriangle },
  ERROR: { color: 'bg-destructive text-destructive-foreground', text: 'Error', icon: AlertTriangle },
  Warning: { color: 'bg-amber-500 text-black', text: 'Warning', icon: AlertTriangle },
  Anomaly: { color: 'bg-purple-500 text-white', text: 'Anomaly', icon: AlertTriangle },
  INFO: { color: 'bg-blue-500 text-white', text: 'Info', icon: AlertTriangle },
};

const AnomalyDetailsModal = ({ anomaly, open, onOpenChange, onAcknowledge }: AnomalyDetailsModalProps) => {
  if (!anomaly) {
    return null;
  }

  const config = severityConfig[anomaly.severity] || severityConfig.Anomaly;
  const SeverityIcon = config.icon;
  let formattedDate = 'Invalid Date';
  let formattedTime = 'Invalid Time';

  // Safely format date/time
  try {
      const date = new Date(anomaly.timestamp);
      if (!isNaN(date.getTime())) {
        formattedDate = date.toLocaleDateString('en-CA'); // YYYY-MM-DD
        formattedTime = date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
      }
  } catch (e) {
      console.error("Error parsing anomaly timestamp:", anomaly.timestamp, e);
  }


  // Handler for the Acknowledge button
  const handleAcknowledgeClick = () => {
    console.log(`Acknowledging anomaly (frontend): ID=${anomaly.id || 'N/A'}, Message=${anomaly.message}`);
    if (onAcknowledge) {
      onAcknowledge(anomaly); // Pass the full anomaly data back
    }
    onOpenChange(false); // Close modal after acknowledging
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[500px] bg-card border-border text-foreground p-6">
        <DialogHeader className="mb-4 text-left">
          <DialogTitle className="flex items-center gap-2 text-lg font-semibold" id="anomaly-dialog-title">
            {/* Adjust icon color based on background for contrast */}
            <SeverityIcon className={cn("h-5 w-5", config.color.includes('amber') ? 'text-black' : 'text-white')} />
            Anomaly Details
          </DialogTitle>
          <DialogDescription id="anomaly-dialog-desc" className="text-muted-foreground pt-1">
            Detailed information about the detected event.
          </DialogDescription>
        </DialogHeader>

        {/* Details Grid */}
        <div className="grid gap-4 py-4 text-sm border-t border-b border-border"> {/* Added borders */}
           {/* Severity and Message */}
           <div className="flex items-center gap-3">
                <Badge variant="default" className={cn(config.color, 'h-6 px-2 font-semibold flex-shrink-0')}>
                    {config.text}
                </Badge>
                <span className="font-medium text-foreground leading-snug">{anomaly.message}</span>
            </div>

            {/* Metadata */}
            <div className="grid grid-cols-[auto_1fr] items-start gap-x-3 gap-y-3 text-muted-foreground pl-2"> {/* Indent metadata slightly */}
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
        </div>

        {/* Footer with Acknowledge Button */}
        <DialogFooter className="mt-6 gap-2 sm:justify-end"> {/* Added gap for spacing */}
          <DialogClose asChild>
              <Button type="button" variant="secondary">Close</Button>
           </DialogClose>
           {/* Only show Acknowledge if handler is provided? Or always show? Showing always for now. */}
           <Button type="button" variant="default" onClick={handleAcknowledgeClick}>
                Acknowledge
           </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default AnomalyDetailsModal;