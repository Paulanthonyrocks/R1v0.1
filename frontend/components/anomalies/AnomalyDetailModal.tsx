// frontend/components/anomalies/AnomalyDetailModal.tsx
import React, { useEffect, useRef } from 'react';
import MatrixButton from "@/components/MatrixButton";
import { Anomaly, SeverityLevel } from '@/lib/types'; // Ensure Anomaly (and thus LocationTuple) is imported
import { Bomb, XOctagon, AlertTriangle, Sigma, InfoIcon } from 'lucide-react'; // Import icons

// Severity to Icon mapping (similar to AnomalyItem)
const severityIconConfig: Record<SeverityLevel, React.ElementType> = {
  Critical: Bomb,
  ERROR: XOctagon,
  Warning: AlertTriangle,
  Anomaly: Sigma,
  INFO: InfoIcon,
};

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
        className="bg-matrix-panel p-6 rounded-lg pixel-drop-shadow max-w-lg w-full text-matrix border border-matrix-border focus:outline-none" // Replaced shadow-xl, Added focus:outline-none for the container
        role="dialog"
        aria-modal="true"
        aria-labelledby={modalTitleId}
      >
        <div className="flex justify-between items-center mb-4">
          <h2 id={modalTitleId} className="text-xl font-bold uppercase tracking-normal">{anomaly.type} - Details</h2> {/* Added tracking-normal */}
          <MatrixButton onClick={onClose}>Close</MatrixButton> {/* Removed custom className */}
        </div>
        <div className="space-y-2 text-sm">
          <p className="tracking-normal"><span className="font-semibold">ID:</span> {anomaly.id}</p> {/* Added tracking-normal */}
          <p className="flex items-center tracking-normal"><span className="font-semibold">Severity:</span> {/* Added tracking-normal */}
            {React.createElement(severityIconConfig[anomaly.severity] || Sigma, { className: "inline ml-1.5 mr-1 h-4 w-4 text-primary" })} {/* Added icon */}
            <span className="capitalize font-bold text-primary">{anomaly.severity}</span>
          </p>
          <p className="tracking-normal"><span className="font-semibold">Description:</span> {anomaly.description}</p> {/* Added tracking-normal */}
          <p className="tracking-normal"><span className="font-semibold">Timestamp:</span> {new Date(anomaly.timestamp).toLocaleString()}</p> {/* Added tracking-normal */}
          <p className="tracking-normal"><span className="font-semibold">Location:</span> Lat: {anomaly.location[0].toFixed(5)}, Lon: {anomaly.location[1].toFixed(5)}</p> {/* Added tracking-normal */}
          {anomaly.resolved && <p className="font-semibold text-muted-foreground tracking-normal">Status: Resolved</p>} {/* Added tracking-normal */}
          {anomaly.details && <p className="tracking-normal"><span className="font-semibold">Additional Details:</span> {anomaly.details}</p>} {/* Added tracking-normal */}
          {anomaly.reportedBy && <p className="tracking-normal"><span className="font-semibold">Reported By:</span> {anomaly.reportedBy}</p>} {/* Added tracking-normal */}
        </div>
      </div>
    </div>
  );
};

export default AnomalyDetailModal;
