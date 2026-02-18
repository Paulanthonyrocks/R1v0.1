
"use client";

import React from 'react';

interface RetroModalProps {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  children: React.ReactNode;
}

const RetroModal: React.FC<RetroModalProps> = ({ isOpen, onClose, title, children }) => {
  if (!isOpen) return null;

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
      <div className="bg-lcd-bg text-lcd-text border-2 border-lcd-text p-4 w-full max-w-sm">
        <header className="flex justify-between items-center border-b-2 border-lcd-text pb-2 mb-4">
          <h2 className="font-bold text-2xl tracking-widest">{title}</h2>
          <button onClick={onClose} className="font-bold text-2xl">X</button>
        </header>
        <main>
          {children}
        </main>
      </div>
    </div>
  );
};

export default RetroModal;
