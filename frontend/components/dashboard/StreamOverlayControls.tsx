import React from 'react';
import { Switch } from '@/components/ui/switch';
import { Label } from '@/components/ui/label';

interface StreamOverlayControlsProps {
  showOverlays: boolean;
  setShowOverlays: (value: boolean) => void;
  showBoundingBoxes: boolean;
  setShowBoundingBoxes: (value: boolean) => void;
  showVehicleDetails: boolean;
  setShowVehicleDetails: (value: boolean) => void;
}

const StreamOverlayControls: React.FC<StreamOverlayControlsProps> = ({
  showOverlays,
  setShowOverlays,
  showBoundingBoxes,
  setShowBoundingBoxes,
  showVehicleDetails,
  setShowVehicleDetails,
}) => {
  return (
    <div className="p-4 space-y-3 bg-black/70 backdrop-blur-sm rounded-none font-lcd matrix-glow text-lcd-text border border-lcd-text">
      <div className="flex items-center justify-between">
        <Label htmlFor="toggle-overlays" className="text-lcd-text text-sm">Show All Overlays</Label>
        <Switch
          id="toggle-overlays"
          checked={showOverlays}
          onCheckedChange={setShowOverlays}
          className="data-[state=checked]:bg-lcd-text data-[state=unchecked]:bg-lcd-bg"
        />
      </div>

      {showOverlays && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <Label htmlFor="toggle-boxes" className="text-lcd-text text-sm">Bounding Boxes</Label>
            <Switch
              id="toggle-boxes"
              checked={showBoundingBoxes}
              onCheckedChange={setShowBoundingBoxes}
              className="data-[state=checked]:bg-lcd-text data-[state=unchecked]:bg-lcd-bg"
            />
          </div>

          <div className="flex items-center justify-between">
            <Label htmlFor="toggle-details" className="text-lcd-text text-sm">Vehicle Details</Label>
            <Switch
              id="toggle-details"
              checked={showVehicleDetails}
              onCheckedChange={setShowVehicleDetails}
              className="data-[state=checked]:bg-lcd-text data-[state=unchecked]:bg-lcd-bg"
            />
          </div>
        </div>
      )}
    </div>
  );
};

export default StreamOverlayControls;
