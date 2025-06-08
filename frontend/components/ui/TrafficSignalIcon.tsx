import React from 'react';

interface TrafficSignalIconProps {
  status: 'red' | 'yellow' | 'green';
  size?: number;
}

const TrafficSignalIcon: React.FC<TrafficSignalIconProps> = ({ status, size = 12 }) => {
  const iconStyle: React.CSSProperties = {
    width: size,
    height: size,
    display: 'inline-block',
    verticalAlign: 'middle',
    imageRendering: 'pixelated', // Ensure crisp rendering if scaled
  };

  const ditherPatternId = `ditherPattern-${Math.random().toString(36).substring(7)}`;

  switch (status) {
    case 'red':
      return (
        <svg style={iconStyle} viewBox="0 0 12 12" xmlns="http://www.w3.org/2000/svg">
          <circle cx="6" cy="6" r="5" fill="black" />
        </svg>
      );
    case 'yellow':
      // Using a simple dither for yellow. fill="url(#pattern_id)"
      // For embedding in HTML, the pattern definition needs to be unique or global.
      // A simple half-fill might be more robust for direct embedding without ID clashes.
      // Let's try a CSS gradient approach for yellow for simplicity here if SVG pattern is tricky.
      // Or, using the dither pattern:
      return (
        <svg style={iconStyle} viewBox="0 0 12 12" xmlns="http://www.w3.org/2000/svg">
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
        <svg style={iconStyle} viewBox="0 0 12 12" xmlns="http://www.w3.org/2000/svg">
          <circle cx="6" cy="6" r="5" fill="none" stroke="black" strokeWidth="1" />
        </svg>
      );
    default:
      return null;
  }
};

export default TrafficSignalIcon;
