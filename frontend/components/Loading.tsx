import React from 'react';

const Loading: React.FC = () => {
  return (
    <div className="fixed top-0 left-0 w-full h-full flex items-center justify-center bg-lcd-bg text-lcd-text font-lcd matrix-glow z-[9999]">
      <div className="flex flex-col items-center">
        <div className="text-4xl mb-4">LOADING...</div>
        <div className="w-32 h-8 border-2 border-lcd-text flex items-center justify-center overflow-hidden">
          <div className="w-full h-full bg-lcd-text animate-loading-bar origin-left"></div>
        </div>
      </div>
    </div>
  );
};

export default Loading;
