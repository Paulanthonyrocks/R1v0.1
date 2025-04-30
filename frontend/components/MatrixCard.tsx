import React, { useState } from 'react';

interface MatrixCardProps {
  title: string;
  content: string;
  icon?: React.ReactNode;
  className?: string;
  colorOverride?: string;
}

const MatrixCard: React.FC<MatrixCardProps> = ({ title, content, icon, className, colorOverride }) => {
  const [isHovered, setIsHovered] = useState(false);

  const cardStyle: React.CSSProperties = {
    backgroundColor: 'var(--matrix-panel)', // Use Matrix panel color
    border: `1px solid var(--matrix-border-color)`, // Subtle border
    transition: 'transform 0.2s ease', // Smooth transition for hover
    transform: isHovered ? 'translateY(-1px)' : 'translateY(0)', // Subtle lift on hover
    borderRadius: '0.5rem',
    padding: '1.5rem',
  };

  const titleStyle: React.CSSProperties = {
    color: colorOverride || 'var(--matrix-text)',
    fontVariant: 'small-caps',
    fontFamily: 'var(--font-mono)',
    fontWeight: 'lighter',
  };

  return (
    <div
      className={`flex flex-col gap-3 ${className}`}
      style={cardStyle}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <div className="flex items-center gap-4">
        {icon && <div className="text-3xl" style={{ color: colorOverride || 'var(--matrix-light)' }}>{icon}</div>}
        <h3 className="text-base" style={titleStyle}>{title}</h3>
      </div>
      <p className="text-sm" style={{ color: 'var(--matrix-muted-text)' }}>{content}</p>
    </div>
  );
};

export default MatrixCard;