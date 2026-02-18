// frontend/components/ui/LoadingMessage.tsx
import React from 'react';

interface LoadingMessageProps {
  text: string;
  className?: string; // Optional className for additional styling
}

const LoadingMessage: React.FC<LoadingMessageProps> = ({ text, className }) => {
  return (
    <div className={`flex justify-center items-center h-64 ${className}`}> {/* Allow parent to specify height or other layout classes */}
      <p className="text-matrix text-xl animate-pulse tracking-normal">{text}</p> {/* Added tracking-normal */}
    </div>
  );
};

export default LoadingMessage;
