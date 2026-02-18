// frontend/components/ui/toast.tsx
import React, { useState, useEffect, createContext, useContext, useCallback } from 'react';
import { cn } from '@/lib/utils';

type ToastType = 'success' | 'error' | 'info' | 'warning';

interface ToastMessage {
  id: string;
  message: string;
  type: ToastType;
}

interface ToastContextType {
  showToast: (message: string, type?: ToastType) => void;
}

const ToastContext = createContext<ToastContextType | undefined>(undefined);

export const ToastProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [toasts, setToasts] = useState<ToastMessage[]>([]);

  const showToast = useCallback((message: string, type: ToastType = 'info') => {
    const id = Date.now().toString();
    setToasts((prevToasts) => [...prevToasts, { id, message, type }]);
  }, []);

  const removeToast = useCallback((id: string) => {
    setToasts((prevToasts) => prevToasts.filter((toast) => toast.id !== id));
  }, []);

  useEffect(() => {
    if (toasts.length > 0) {
      const timer = setTimeout(() => {
        removeToast(toasts[0].id);
      }, 3000); // Toast disappears after 3 seconds
      return () => clearTimeout(timer);
    }
  }, [toasts, removeToast]);

  return (
    <ToastContext.Provider value={{ showToast }}>
      {children}
      <div className="fixed bottom-4 right-4 z-[9999] space-y-2">
        {toasts.map((toast) => (
          <Toast
            key={toast.id}
            message={toast.message}
            type={toast.type}
            onClose={() => removeToast(toast.id)}
          />
        ))}
      </div>
    </ToastContext.Provider>
  );
};

export const useToast = () => {
  const context = useContext(ToastContext);
  if (context === undefined) {
    throw new Error('useToast must be used within a ToastProvider');
  }
  return context;
};

interface ToastProps {
  message: string;
  type: ToastType;
  onClose: () => void;
}

const Toast: React.FC<ToastProps> = ({ message, type, onClose }) => {
  const typeClasses = {
    success: 'border-green-500 text-green-500',
    error: 'border-red-500 text-red-500',
    info: 'border-blue-500 text-blue-500',
    warning: 'border-yellow-500 text-yellow-500',
  };

  return (
    <div
      className={cn(
        'matrix-card p-3 border-2 font-lcd matrix-glow',
        typeClasses[type],
        'flex items-center justify-between gap-4'
      )}
      role="alert"
    >
      <span>{message}</span>
      <button onClick={onClose} className="text-lcd-text hover:text-lcd-text/80 font-bold">
        X
      </button>
    </div>
  );
};
