import React from 'react';

interface ErrorProps {
  message: string;
}

const Error: React.FC<ErrorProps> = ({ message }) => {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-lcd-bg/80">
      <div className="matrix-card p-4 text-center border-2 border-lcd-text font-lcd matrix-glow">
        <p className="text-xl mb-2">ERROR!</p>
        <p>{message}</p>
        <p className="text-sm mt-2">Please refresh the page or contact support.</p>
      </div>
    </div>
  );
};

export default Error;