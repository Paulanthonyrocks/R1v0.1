// components/dashboard/AnomalyItem.tsx
import React from 'react';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { AnomalyItemProps, AlertData, SeverityLevel } from '@/lib/types'; // Import AlertData
import { Bomb, XOctagon, AlertTriangle, Sigma, InfoIcon } from 'lucide-react'; // Import new icons

const MONOCHROME_BADGE_STYLE = 'bg-primary text-primary-foreground'; // Black badge, green text

interface SeverityConfigEntry {
  styleClass: string; // Renamed 'color' to 'styleClass' for clarity
  text: string;
  icon: React.ElementType;
}

const severityConfig: Record<SeverityLevel, SeverityConfigEntry> = {
  Critical: { styleClass: MONOCHROME_BADGE_STYLE, text: 'Critical', icon: Bomb },
  ERROR: { styleClass: MONOCHROME_BADGE_STYLE, text: 'Error', icon: XOctagon },
  Warning: { styleClass: MONOCHROME_BADGE_STYLE, text: 'Warning', icon: AlertTriangle },
  Anomaly: { styleClass: MONOCHROME_BADGE_STYLE, text: 'Anomaly', icon: Sigma },
  INFO: { styleClass: MONOCHROME_BADGE_STYLE, text: 'Info', icon: InfoIcon },
};

// Accept the full AlertData object as props
const AnomalyItem = React.memo((props: AnomalyItemProps) => {
  const { id, timestamp, severity, feed_id, message, onSelect, ...rest } = props; // Destructure props
  const config = severityConfig[severity] || severityConfig.Anomaly; // Fallback to Anomaly for unknown severities
  const IconComponent = config.icon;
  const displayTime = new Date(timestamp).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });

  // Function to call the onSelect prop with the full alert data
  const handleSelect = () => {
    if (onSelect) {
        // Pass the full alert object back to the parent
        const fullAlertData: AlertData = { id, timestamp, severity, feed_id, message, ...rest };
        onSelect(fullAlertData);
    } else {
        // Fallback console log if no handler passed
        console.log(`Selected anomaly: ID=${id || 'N/A'}, Timestamp=${timestamp}`);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      handleSelect();
    }
  };

  // Construct aria-label with key details
  const ariaLabel = `View details for ${severity} anomaly at ${displayTime}: ${message}${feed_id ? ` from feed ${feed_id}` : ''}`;

  return (
    <div
      tabIndex={0}
      role="button"
      aria-label={ariaLabel}
      onClick={handleSelect}
      onKeyDown={handleKeyDown}
      className={cn(
        'p-4 flex items-start gap-4 cursor-pointer hover:bg-muted/50 transition-colors duration-150', // Use theme hover
        'focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none focus-visible:bg-muted/60 rounded-sm' // Use focus-visible
      )}
    >
      <Badge variant="default" className={cn(config.styleClass, 'h-6 px-2 flex items-center flex-shrink-0 font-semibold tracking-normal')}> {/* Added tracking-normal, flex, items-center */}
        <IconComponent className="mr-1.5 h-3 w-3" /> {/* Icon color will be text-primary-foreground (green) due to Badge style */}
        {config.text}
      </Badge>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-foreground truncate tracking-normal" title={message}>{message}</p> {/* Added tracking-normal */}
        <div className="flex items-center gap-2 text-[10px] text-muted-foreground mt-0.5 tracking-normal"> {/* Added tracking-normal */}
          <span>{displayTime}</span>
          {feed_id && <span>• Feed {feed_id}</span>}
          {/* Display location/description if available */}
          {rest.location && <span title={rest.location}>• {rest.location.substring(0, 20)}{rest.location.length > 20 ? '...' : ''}</span>}
          {rest.description && <span title={rest.description}>• {rest.description.substring(0, 30)}{rest.description.length > 30 ? '...' : ''}</span>}
        </div>
      </div>
    </div>
  );
});

AnomalyItem.displayName = 'AnomalyItem';
export default AnomalyItem;