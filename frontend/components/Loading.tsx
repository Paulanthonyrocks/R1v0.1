tsx
import React from 'react';

const Loading: React.FC = () => {
  return (
    <div className="fixed top-0 left-0 w-full h-full flex items-center justify-center bg-gray-100 bg-opacity-75">
      <div className="text-blue-500 text-2xl">Loading...</div>
    </div>
  );
};

export default Loading;