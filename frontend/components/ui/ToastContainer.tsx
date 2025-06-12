// frontend/components/ui/ToastContainer.tsx
import React from 'react';
import { CheckCircle2, AlertTriangle } from 'lucide-react'; // Import icons

export interface ToastMessage {
  id: number;
  message: string;
  type: 'success' | 'error';
}

interface ToastContainerProps {
  toasts: ToastMessage[];
  removeToast: (id: number) => void;
}

const ToastContainer: React.FC<ToastContainerProps> = ({ toasts, removeToast }) => {
  if (!toasts.length) return null;
  return (
    <div className="fixed bottom-4 right-4 z-[100] space-y-2">
      {toasts.map(toast => (
        <div
          key={toast.id}
          // Error toasts now also use primary styling for monochrome theme
          className={`p-3 rounded-md shadow-lg flex items-center tracking-normal ${toast.type === 'success' ? 'bg-primary text-primary-foreground' : 'bg-primary text-primary-foreground'}`} {/* Added tracking-normal */}
          onClick={() => removeToast(toast.id)}
        >
          {toast.type === 'success' ?
            <CheckCircle2 className="mr-2 h-5 w-5 text-primary-foreground" /> :
            <AlertTriangle className="mr-2 h-5 w-5 text-primary-foreground" />}
          {toast.message}
        </div>
      ))}
    </div>
  );
};

export default ToastContainer;
