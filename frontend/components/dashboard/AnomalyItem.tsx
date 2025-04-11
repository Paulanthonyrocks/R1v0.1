// components/dashboard/AnomalyItem.tsx
import React from 'react';
import { Badge } from '@/components/ui/badge';
import { cn } from '@/lib/utils';
import { AnomalyItemProps, AlertData, SeverityLevel } from '@/lib/types'; // Import AlertData

const severityConfig: Record<SeverityLevel, { color: string; text: string }> = {
  Critical: { color: 'bg-destructive text-destructive-foreground', text: 'Critical' },
  ERROR: { color: 'bg-destructive text-destructive-foreground', text: 'Error' },
  Warning: { color: 'bg-amber-500 text-black', text: 'Warning' },
  Anomaly: { color: 'bg-purple-500 text-white', text: 'Anomaly' },
  INFO: { color: 'bg-blue-500 text-white', text: 'Info' },
};

// Accept the full AlertData object as props
const AnomalyItem = React.memo((props: AnomalyItemProps) => {
  const { id, timestamp, severity, feed_id, message, onSelect, ...rest } = props; // Destructure props
  const config = severityConfig[severity] || severityConfig.Anomaly;
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
      <Badge variant="default" className={cn(config.color, 'h-6 px-2 flex-shrink-0 font-semibold')}>
        {config.text}
      </Badge>
      <div className="flex-1 min-w-0">
        <p className="text-sm font-medium text-foreground truncate" title={message}>{message}</p> {/* Add title for full text on hover */}
        <div className="flex items-center gap-2 text-[10px] text-muted-foreground mt-0.5">
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