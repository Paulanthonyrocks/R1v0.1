import React from 'react';
import { Label } from '@/components/ui/label';

interface StreamOverlayControlsProps {
  showOverlays: boolean;
  setShowOverlays: (value: boolean) => void;
  showBoundingBoxes: boolean;
  setShowBoundingBoxes: (value: boolean) => void;
  showVehicleDetails: boolean;
  setShowVehicleDetails: (value: boolean) => void;
  showROI: boolean;
  setShowROI: (value: boolean) => void;
  controlId: string;
}

const MatrixCheckbox = ({ id, checked, onCheckedChange }: { id: string, checked: boolean, onCheckedChange: (c: boolean) => void }) => (
  <div className="relative flex items-center">
    <input
      type="checkbox"
      id={id}
      checked={checked}
      onChange={(e) => onCheckedChange(e.target.checked)}
      className="peer h-5 w-5 appearance-none border-2 border-lcd-bg bg-transparent rounded-none checked:bg-lcd-bg cursor-pointer transition-colors focus:outline-none focus:ring-1 focus:ring-lcd-bg"
    />
    <svg
      className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-4 h-4 pointer-events-none hidden peer-checked:block text-lcd-text"
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="4"
      strokeLinecap="square"
      strokeLinejoin="miter"
    >
      <polyline points="20 6 9 17 4 12" />
    </svg>
  </div>
);

const StreamOverlayControls: React.FC<StreamOverlayControlsProps> = ({
  showOverlays,
  setShowOverlays,
  showBoundingBoxes,
  setShowBoundingBoxes,
  showVehicleDetails,
  setShowVehicleDetails,
  showROI,
  setShowROI,
  controlId,
}) => {
  return (
    <div 
      className="p-4 space-y-3 bg-black/90 backdrop-blur-md rounded-none font-lcd matrix-glow text-lcd-bg border border-lcd-bg shadow-[0_0_10px_rgba(182,255,176,0.2)]"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="flex items-center justify-between gap-4">
        <Label htmlFor={`toggle-overlays-${controlId}`} className="text-lcd-bg text-sm cursor-pointer uppercase font-bold tracking-wider">Show All Overlays</Label>
        <MatrixCheckbox
          id={`toggle-overlays-${controlId}`}
          checked={showOverlays}
          onCheckedChange={setShowOverlays}
        />
      </div>

      {showOverlays && (
        <div className="space-y-3 pl-2 border-l border-lcd-bg/30">
          <div className="flex items-center justify-between gap-4">
            <Label htmlFor={`toggle-boxes-${controlId}`} className="text-lcd-bg/80 text-sm cursor-pointer uppercase tracking-wide">Bounding Boxes</Label>
            <MatrixCheckbox
              id={`toggle-boxes-${controlId}`}
              checked={showBoundingBoxes}
              onCheckedChange={setShowBoundingBoxes}
            />
          </div>

          <div className="flex items-center justify-between gap-4">
            <Label htmlFor={`toggle-details-${controlId}`} className="text-lcd-bg/80 text-sm cursor-pointer uppercase tracking-wide">Vehicle Details</Label>
            <MatrixCheckbox
              id={`toggle-details-${controlId}`}
              checked={showVehicleDetails}
              onCheckedChange={setShowVehicleDetails}
            />
          </div>

          <div className="flex items-center justify-between gap-4">
            <Label htmlFor={`toggle-roi-${controlId}`} className="text-lcd-bg/80 text-sm cursor-pointer uppercase tracking-wide">Region of Interest</Label>
            <MatrixCheckbox
              id={`toggle-roi-${controlId}`}
              checked={showROI}
              onCheckedChange={setShowROI}
            />
          </div>
        </div>
      )}
    </div>
  );
};

export default StreamOverlayControls;
