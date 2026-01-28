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
  showExclusionZones?: boolean;
  setShowExclusionZones?: (value: boolean) => void;
  onClearExclusionZones?: () => void;
  staticFilterEnabled?: boolean;
  setStaticFilterEnabled?: (value: boolean) => void;
  showTrajectories: boolean;
  setShowTrajectories: (value: boolean) => void;
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
  showExclusionZones,
  setShowExclusionZones,
  onClearExclusionZones,
  staticFilterEnabled,
  setStaticFilterEnabled,
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

          <div className="flex items-center justify-between gap-4">
            <Label htmlFor={`toggle-trajectories-${controlId}`} className="text-lcd-bg/80 text-sm cursor-pointer uppercase tracking-wide">Trajectories</Label>
            <MatrixCheckbox
              id={`toggle-trajectories-${controlId}`}
              checked={showTrajectories}
              onCheckedChange={setShowTrajectories}
            />
          </div>

          {setShowExclusionZones && (
            <div className="flex items-center justify-between gap-4">
              <Label htmlFor={`toggle-excl-${controlId}`} className="text-lcd-bg/80 text-sm cursor-pointer uppercase tracking-wide">Exclusion Zones</Label>
              <div className="flex items-center gap-2">
                {onClearExclusionZones && (
                  <button
                    onClick={onClearExclusionZones}
                    className="text-[10px] bg-red-900/50 hover:bg-red-900 px-1 border border-red-500 text-white uppercase"
                  >
                    Clear All
                  </button>
                )}
                <MatrixCheckbox
                  id={`toggle-excl-${controlId}`}
                  checked={!!showExclusionZones}
                  onCheckedChange={setShowExclusionZones}
                />
              </div>
            </div>
          )}

          {setStaticFilterEnabled && (
            <div className="flex items-center justify-between gap-4 pt-2 border-t border-lcd-bg/30">
              <Label htmlFor={`toggle-static-${controlId}`} className="text-lcd-bg/80 text-sm cursor-pointer uppercase tracking-wide">Static Filter</Label>
              <MatrixCheckbox
                id={`toggle-static-${controlId}`}
                checked={!!staticFilterEnabled}
                onCheckedChange={setStaticFilterEnabled}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default StreamOverlayControls;
