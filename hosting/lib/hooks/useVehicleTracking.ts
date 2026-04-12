import { useState, useCallback, useRef } from 'react';
import { VehicleData } from '../types/api';

interface VehicleStateMap {
    [key: string]: VehicleData;
}

export const useVehicleTracking = () => {
    const vehicleStateRef = useRef<VehicleStateMap>({});

    const mergeVehicleUpdates = useCallback((updates: VehicleData[]) => {
        const currentParams = vehicleStateRef.current;
        const newFrameIds = new Set<string>();
        
        updates.forEach(update => {
            const vid = update.vehicle_id;
            newFrameIds.add(vid);

            const existing = currentParams[vid];
            if (existing) {
                currentParams[vid] = { ...existing, ...update };
            } else {
                currentParams[vid] = update;
            }
        });

        Object.keys(currentParams).forEach(vid => {
            if (!newFrameIds.has(vid)) {
                delete currentParams[vid];
            }
        });
    }, []);

    const getVehicles = useCallback(() => {
        return Object.values(vehicleStateRef.current);
    }, []);

    return { vehicles: vehicleStateRef, mergeVehicleUpdates, getVehicles };
};
