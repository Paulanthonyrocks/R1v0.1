import React from 'react';

// --- Module-level Constants for SVG Data URLs ---
const SVG_DITHER_PATTERNS = {
  low: `url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"><rect width="2" height="2" fill="%238CA17C"/><rect width="1" height="1" fill="black"/></svg>')`,
  medium: `url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"><rect width="2" height="2" fill="%238CA17C"/><rect width="1" height="1" fill="black"/><rect x="1" y="1" width="1" height="1" fill="black"/></svg>')`,
  high: `url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2" fill="%238CA17C"><rect x="0" y="0" width="1" height="1" fill="black"/><rect x="1" y="0" width="1" height="1" fill="black"/><rect x="0" y="1" width="1" height="1" fill="black"/></svg>')`,
};

const VALID_FLOW_STATUSES = ['low', 'medium', 'high'];
const DEFAULT_WIDTH = '24px';
const DEFAULT_HEIGHT = '12px';

interface DitheredTrafficIndicatorProps {
  flow: 'low' | 'medium' | 'high' | string; // Allow string for invalid case
  width?: string | number;
  height?: string | number;
}

// Helper function to parse and validate dimensions
const parseDimension = (value: string | number | undefined, defaultValue: string, dimensionName: string): string => {
  if (typeof value === 'number') {
    return value > 0 ? `${value}px` : defaultValue;
  }
  if (typeof value === 'string') {
    // Check if it's a number-like string (e.g., "24") or includes units (e.g., "24px", "1em")
    if (/^\d+(\.\d+)?(px|em|rem|%|vw|vh)?$/.test(value)) {
      const numericPart = parseFloat(value);
      if (numericPart < 0) {
        console.warn(`DitheredTrafficIndicator: Invalid negative ${dimensionName} "${value}". Using default ${defaultValue}.`);
        return defaultValue;
      }
      return /^\d+(\.\d+)?$/.test(value) ? `${value}px` : value; // Append px if no unit
    } else {
      console.warn(`DitheredTrafficIndicator: Invalid ${dimensionName} string "${value}". Using default ${defaultValue}.`);
      return defaultValue;
    }
  }
  return defaultValue; // Fallback for undefined or other types
};

const DitheredTrafficIndicator: React.FC<DitheredTrafficIndicatorProps> = ({
  flow,
  width: widthProp, // Renamed to avoid conflict with outer scope/defaults if any
  height: heightProp,
}) => {
  // Validate flow prop
  if (!VALID_FLOW_STATUSES.includes(flow)) {
    console.warn(`DitheredTrafficIndicator: Invalid flow status "${flow}". Expected one of ${VALID_FLOW_STATUSES.join(', ')}. Rendering null.`);
    return null;
  }

  const validatedWidth = parseDimension(widthProp, DEFAULT_WIDTH, 'width');
  const validatedHeight = parseDimension(heightProp, DEFAULT_HEIGHT, 'height');

  const indicatorStyle: React.CSSProperties = {
    width: validatedWidth,
    height: validatedHeight,
    backgroundColor: '#8CA17C', // Fallback green
    backgroundImage: SVG_DITHER_PATTERNS[flow as 'low' | 'medium' | 'high'], // Type assertion after validation
    backgroundRepeat: 'repeat',
    imageRendering: 'pixelated',
    display: 'inline-block',
    verticalAlign: 'middle',
    border: '1px solid black', // Add a border to define the area
  };

  return (
      <div style={indicatorStyle} aria-label={`Traffic flow: ${flow}`} />
  );
};

export default DitheredTrafficIndicator;
