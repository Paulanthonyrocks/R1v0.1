import { useState, useCallback, useRef } from 'react';
import { VehicleData } from '../types/api';

interface VehicleStateMap {
    [key: string]: VehicleData;
}

export const useVehicleTracking = () => {
    const [vehicles, setVehicles] = useState<VehicleData[]>([]);
    const vehicleStateRef = useRef<VehicleStateMap>({});

    const mergeVehicleUpdates = useCallback((updates: VehicleData[]) => {
        const currentParams = vehicleStateRef.current;
        const newFrameIds = new Set<string>();
        const mergedList: VehicleData[] = [];

        updates.forEach(update => {
            const vid = update.vehicle_id;
            newFrameIds.add(vid);

            const existing = currentParams[vid];

            // If we have existing state, merge updates into it
            // Only fields present in 'update' (delta) will overwrite existing
            if (existing) {
                // Create a new merged object
                // Note: 'update' might have undefined fields if they were omitted by backend
                // but JSON.parse usually drops undefined keys, so check keys existence if needed
                // Backend sends dictionary, so keys are missing, not undefined.

                // Simple spread works because update only contains CHANGED or MANDATORY fields.
                // Static fields (class_name etc) in 'existing' are preserved if not in 'update'.
                const merged = { ...existing, ...update };
                currentParams[vid] = merged;
                mergedList.push(merged);
            } else {
                // New vehicle or Keyframe full update
                // If it's a delta but we don't have state, we might have issues 
                // (e.g. missing class_name). But backend sends FULL update for new IDs.
                currentParams[vid] = update;
                mergedList.push(update);
            }
        });

        // Prune vehicles not in this frame
        // (Assuming 'updates' contains ALL active vehicles for this frame, 
        // even if some are deltas. Backend sends entry for every active vehicle).
        Object.keys(currentParams).forEach(vid => {
            if (!newFrameIds.has(vid)) {
                delete currentParams[vid];
            }
        });

        vehicleStateRef.current = currentParams;
        setVehicles(mergedList);
    }, []);

    return { vehicles, mergeVehicleUpdates };
};
