import React from 'react';

interface DitheredTrafficIndicatorProps {
  flow: 'low' | 'medium' | 'high';
  width?: string | number;
  height?: string | number;
}

const DitheredTrafficIndicator: React.FC<DitheredTrafficIndicatorProps> = ({
  flow,
  width = '100%', // Default to full width of its container
  height = '1em', // Default to height of a line of text
}) => {
  const indicatorStyle: React.CSSProperties = {
    width: width,
    height: height,
    backgroundColor: '#8CA17C', // Fallback green
    backgroundRepeat: 'repeat',
    imageRendering: 'pixelated',
    display: 'inline-block',
    verticalAlign: 'middle',
    border: '1px solid black', // Add a border to define the area
  };

  let svgBackgroundImage = '';
  // Note: %23 is the URL encoding for #
  if (flow === 'low') { // 1 black pixel, 3 green pixels
    svgBackgroundImage = `url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"><rect width="2" height="2" fill="%238CA17C"/><rect width="1" height="1" fill="black"/></svg>')`;
  } else if (flow === 'medium') { // 2 black pixels, 2 green pixels (checkerboard)
    svgBackgroundImage = `url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"><rect width="2" height="2" fill="%238CA17C"/><rect width="1" height="1" fill="black"/><rect x="1" y="1" width="1" height="1" fill="black"/></svg>')`;
  } else if (flow === 'high') { // 3 black pixels, 1 green pixel
    svgBackgroundImage = `url('data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2" fill="%238CA17C"><rect x="0" y="0" width="1" height="1" fill="black"/><rect x="1" y="0" width="1" height="1" fill="black"/><rect x="0" y="1" width="1" height="1" fill="black"/></svg>')`;
  }

  indicatorStyle.backgroundImage = svgBackgroundImage;

  return (
      <div style={indicatorStyle} />
  );
};

export default DitheredTrafficIndicator;
