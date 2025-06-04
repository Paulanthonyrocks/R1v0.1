// frontend/components/anomalies/AnomalyDetailModal.tsx
import React, { useEffect, useRef } from 'react';
import MatrixButton from "@/components/MatrixButton";
import { Anomaly } from '@/lib/types'; // Ensure Anomaly (and thus LocationTuple) is imported

interface AnomalyDetailModalProps {
  anomaly: Anomaly | null;
  onClose: () => void;
}

const AnomalyDetailModal: React.FC<AnomalyDetailModalProps> = ({ anomaly, onClose }) => {
  if (!anomaly) return null;

  const modalTitleId = `anomaly-modal-title-${anomaly.id}`;
  const modalRef = useRef<HTMLDivElement>(null);
  const previousFocusRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (anomaly) {
      previousFocusRef.current = document.activeElement as HTMLElement;
      modalRef.current?.focus(); // Focus the modal container

      const handleKeyDown = (event: KeyboardEvent) => {
        if (event.key === 'Escape') {
          onClose();
        }
        if (event.key === 'Tab') {
          const focusableElements = modalRef.current?.querySelectorAll(
            'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'
          );
          if (focusableElements && focusableElements.length > 0) {
            const firstElement = focusableElements[0] as HTMLElement;
            const lastElement = focusableElements[focusableElements.length - 1] as HTMLElement;

            if (event.shiftKey) { // Shift + Tab
              if (document.activeElement === firstElement) {
                lastElement.focus();
                event.preventDefault();
              }
            } else { // Tab
              if (document.activeElement === lastElement) {
                firstElement.focus();
                event.preventDefault();
              }
            }
          }
        }
      };

      document.addEventListener('keydown', handleKeyDown);
      return () => {
        document.removeEventListener('keydown', handleKeyDown);
        previousFocusRef.current?.focus(); // Restore focus on close
      };
    }
  }, [anomaly, onClose]);

  return (
    <div className="fixed inset-0 bg-black bg-opacity-75 flex items-center justify-center z-50 p-4">
      <div
        ref={modalRef}
        tabIndex={-1} // Make the modal container focusable
        className="bg-matrix-panel p-6 rounded-lg shadow-xl max-w-lg w-full text-matrix border border-matrix-border focus:outline-none" // Added focus:outline-none for the container
        role="dialog"
        aria-modal="true"
        aria-labelledby={modalTitleId}
      >
        <div className="flex justify-between items-center mb-4">
          <h2 id={modalTitleId} className="text-xl font-bold uppercase">{anomaly.type} - Details</h2>
          <MatrixButton onClick={onClose} className="bg-card hover:bg-card/80">Close</MatrixButton>
        </div>
        <div className="space-y-2 text-sm">
          <p><span className="font-semibold">ID:</span> {anomaly.id}</p>
          <p><span className="font-semibold">Severity:</span> <span className={`capitalize font-bold ${
             anomaly.severity === "high" ? "text-destructive" :
             anomaly.severity === "medium" ? "text-yellow-500" :
             "text-green-500"
          }`}>{anomaly.severity}</span></p>
          <p><span className="font-semibold">Description:</span> {anomaly.description}</p>
          <p><span className="font-semibold">Timestamp:</span> {new Date(anomaly.timestamp).toLocaleString()}</p>
          <p><span className="font-semibold">Location:</span> Lat: {anomaly.location[0].toFixed(5)}, Lon: {anomaly.location[1].toFixed(5)}</p>
          {anomaly.resolved && <p className="font-semibold text-muted-foreground">Status: Resolved</p>}
          {anomaly.details && <p><span className="font-semibold">Additional Details:</span> {anomaly.details}</p>}
          {anomaly.reportedBy && <p><span className="font-semibold">Reported By:</span> {anomaly.reportedBy}</p>}
        </div>
      </div>
    </div>
  );
};

export default AnomalyDetailModal;
