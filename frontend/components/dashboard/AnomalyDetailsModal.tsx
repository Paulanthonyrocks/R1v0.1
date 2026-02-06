import { incidentService } from '@/lib/services/incidentService';

// ... (existing imports and severityConfig) ...

const AnomalyDetailsModal = ({ anomaly, open, onOpenChange, onAcknowledge }: AnomalyDetailsModalProps) => {
  const [isUpdating, setIsUpdating] = React.useState(false);
  const [notes, setNotes] = React.useState('');
  const [showResolveInput, setShowResolveInput] = React.useState(false);

  if (!anomaly) {
    return null;
  }

  // ... (date formatting logic) ...

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

  const handleResolve = async () => {
    if (!anomaly.id) return;
    setIsUpdating(true);
    const success = await incidentService.resolveIncident(String(anomaly.id), notes);
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
            <SeverityIcon className={cn("h-5 w-5", config.color.includes('amber') ? 'text-black' : 'text-white')} />
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

          {showResolveInput && (
            <div className="mt-2 space-y-2">
              <label className="text-[10px] uppercase font-bold text-muted-foreground">Resolution Notes</label>
              <textarea
                className="w-full bg-background border border-border p-2 rounded text-xs min-h-[60px]"
                placeholder="Enter resolution details..."
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
              />
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