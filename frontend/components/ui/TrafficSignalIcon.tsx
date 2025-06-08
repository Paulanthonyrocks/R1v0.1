import React, { useId } from 'react'; // Import useId

interface TrafficSignalIconProps {
  status: 'red' | 'yellow' | 'green' | string; // Allow string for invalid case, but primarily expect the union type
  size?: number;
}

const VALID_STATUSES = ['red', 'yellow', 'green'];

const TrafficSignalIcon: React.FC<TrafficSignalIconProps> = ({ status, size = 12 }) => {
  // Fallback handling for invalid status
  if (!VALID_STATUSES.includes(status)) {
    console.warn(`TrafficSignalIcon: Invalid status "${status}" provided. Expected one of ${VALID_STATUSES.join(', ')}. Rendering null.`);
    return null;
  }

  const iconStyle: React.CSSProperties = {
    width: size,
    height: size,
    display: 'inline-block',
    verticalAlign: 'middle',
    imageRendering: 'pixelated', // Ensure crisp rendering if scaled
  };

  // Deterministic (but unique per instance) ID for the pattern
  const uniqueIdSuffix = useId();
  const ditherPatternId = `dither-pattern-yellow-signal-${uniqueIdSuffix}`;

  switch (status) {
    case 'red':
      return (
        <svg style={iconStyle} viewBox="0 0 12 12" xmlns="http://www.w3.org/2000/svg" aria-label="Red traffic signal">
          <circle cx="6" cy="6" r="5" fill="black" />
        </svg>
      );
    case 'yellow':
      return (
        <svg style={iconStyle} viewBox="0 0 12 12" xmlns="http://www.w3.org/2000/svg" aria-label="Yellow traffic signal">
          <defs>
            <pattern id={ditherPatternId} width="2" height="2" patternUnits="userSpaceOnUse">
              <rect width="1" height="1" fill="black" />
              <rect x="1" y="1" width="1" height="1" fill="black" />
            </pattern>
          </defs>
          <circle cx="6" cy="6" r="5" fill={`url(#${ditherPatternId})`} stroke="black" strokeWidth="1"/>
        </svg>
      );
    case 'green':
      return (
        <svg style={iconStyle} viewBox="0 0 12 12" xmlns="http://www.w3.org/2000/svg" aria-label="Green traffic signal">
          <circle cx="6" cy="6" r="5" fill="none" stroke="black" strokeWidth="1" />
        </svg>
      );
    // No default case needed here due to the upfront validation,
    // but TypeScript might still want it if status type isn't strictly narrowed.
    // The initial check ensures status is one of 'red', 'yellow', 'green' by this point.
  }
  // Should be unreachable if VALID_STATUSES covers all switch cases.
  return null;
};

export default TrafficSignalIcon;
