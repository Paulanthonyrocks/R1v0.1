// components/dashboard/AnomalyItem.tsx
import React from 'react';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { AnomalyItemProps, AlertData, SeverityLevel } from '@/lib/types';
import { Bomb, XOctagon, AlertTriangle, Sigma, InfoIcon, ChevronRight } from 'lucide-react';

const MONOCHROME_BADGE_STYLE = 'bg-primary text-primary-foreground';

interface SeverityConfigEntry {
  styleClass: string;
  text: string;
  icon: React.ElementType;
}

const severityConfig: Record<SeverityLevel, SeverityConfigEntry> = {
  Critical: { styleClass: 'bg-destructive text-destructive-foreground', text: 'Critical', icon: Bomb },
  ERROR: { styleClass: 'bg-destructive text-destructive-foreground', text: 'Error', icon: XOctagon },
  Warning: { styleClass: 'bg-warning text-warning-foreground', text: 'Warning', icon: AlertTriangle },
  Anomaly: { styleClass: MONOCHROME_BADGE_STYLE, text: 'Anomaly', icon: Sigma },
  INFO: { styleClass: 'bg-info text-info-foreground', text: 'Info', icon: InfoIcon },
};

const AnomalyItem = React.memo((props: AnomalyItemProps) => {
  const { id, timestamp, severity, feed_id, message, status, onSelect, ...rest } = props;
  const config = severityConfig[severity] || severityConfig.Anomaly;
  const IconComponent = config.icon;
  const displayTime = new Date(timestamp).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

  const handleSelect = () => {
    if (onSelect) {
      const fullAlertData: AlertData = { id, timestamp, severity, feed_id, message, status, ...rest };
      onSelect(fullAlertData);
    } else {
      console.log(`Selected incident: ID=${id || 'N/A'}, Status=${status || 'REPORTED'}`);
    }
  };

  const statusColors: Record<string, string> = {
    REPORTED: 'text-destructive border-destructive/30',
    ACKNOWLEDGED: 'text-warning border-warning/30',
    RESOLVED: 'text-matrix-light border-matrix-light/30',
  };

  const ariaLabel = `View details for ${severity} incident at ${displayTime}: ${message}${feed_id ? ` from feed ${feed_id}` : ''}. Status: ${status || 'REPORTED'}`;

  return (
    <div
      tabIndex={0}
      role="button"
      aria-label={ariaLabel}
      onClick={handleSelect}
      onKeyDown={handleKeyDown}
      className={cn(
        'group p-3 flex items-start gap-3 cursor-pointer border border-lcd-text/20 hover:border-lcd-text transition-all duration-200',
        'bg-lcd-bg/5 hover:bg-lcd-text hover:text-lcd-bg',
        status === 'RESOLVED' && 'opacity-60 grayscale-[0.5]'
      )}
    >
      <Badge variant="default" className={cn(config.styleClass, 'h-6 px-2 flex items-center flex-shrink-0 font-semibold tracking-normal rounded-none font-lcd', 'group-hover:opacity-90')}>
        <IconComponent className="mr-1.5 h-3 w-3" />
        {config.text}
      </Badge>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 mb-0.5">
          <p className="text-sm font-medium truncate tracking-wide font-lcd group-hover:text-lcd-bg" title={message}>{message}</p>
          {status && (
            <span className={cn("text-[8px] px-1 border border-transparent font-mono uppercase bg-black/20", statusColors[status] || statusColors.REPORTED, 'group-hover:bg-black/40')}>
              {status}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2 text-[10px] opacity-70 group-hover:text-lcd-bg group-hover:opacity-90 mt-1 tracking-wider font-lcd">
          <span>{displayTime}</span>
          {feed_id && <span>• FEED {feed_id}</span>}
        </div>
      </div>
      <ChevronRight className="h-4 w-4 opacity-0 group-hover:opacity-100 transition-opacity self-center group-hover:text-lcd-bg" />
    </div>
  );
});

AnomalyItem.displayName = 'AnomalyItem';
export default AnomalyItem;