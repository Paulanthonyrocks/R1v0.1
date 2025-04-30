tsx
import React from 'react';

interface ErrorProps {
  message: string;
}

const Error: React.FC<ErrorProps> = ({ message }) => {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="p-4 rounded-md text-white bg-red-500 text-center">
        {message}
      </div>
    </div>
  );
};

export default Error;