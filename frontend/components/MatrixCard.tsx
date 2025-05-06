import React, { useState } from 'react';

interface MatrixCardProps {
  title: string;
  content?: string;
  icon?: React.ReactNode;
  className?: string;
  colorOverride?: string;
  children?: React.ReactNode;
}

const MatrixCard: React.FC<MatrixCardProps> = ({ title, content, icon, className, colorOverride, children }) => {
  const [isHovered, setIsHovered] = useState(false);

  return (
    <div
      className={`matrix-card ${isHovered ? 'matrix-card--hover' : 'matrix-card--default'} flex flex-col gap-3 ${className || ''}`}
      {...(colorOverride ? { 'data-color-override': 'true', style: { '--color-override': colorOverride } as React.CSSProperties } : {})}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <div className="flex items-center gap-4">
        {icon && (
          <div className="text-3xl matrix-card__icon">
            {icon}
          </div>
        )}
        <h3 className="text-base matrix-card__title">
          {title}
        </h3>
      </div>
      {content && <p className="text-sm matrix-card__content">{content}</p>}
      {children}
    </div>
  );
};

export default MatrixCard;