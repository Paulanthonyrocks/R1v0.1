import React from 'react';

interface ParticlesProps {
  className?: string;
}

const Particles: React.FC<ParticlesProps> = ({ className }) => {
  return (
    <div className={className}>
      {/* Placeholder for particles animation */}
      <div className="absolute inset-0 flex items-center justify-center text-gray-500 text-xs">
        [Particles Placeholder]
      </div>
    </div>
  );
};

export default Particles;
