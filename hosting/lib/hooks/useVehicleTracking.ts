import { useState, useCallback, useRef } from 'react';
import { VehicleData } from '../types/api';

interface InternalVehicleState extends VehicleData {
    last_update: number;
}

interface VehicleStateMap {
    [key: string]: InternalVehicleState;
}

export const useVehicleTracking = () => {
    const vehicleStateRef = useRef<VehicleStateMap>({});

    const mergeVehicleUpdates = useCallback((updates: any[]) => {
        const currentParams = vehicleStateRef.current;
        const now = Date.now();
        
        updates.forEach(update => {
            const vid = update.vehicle_id;
            if (!vid) return;

            const existing = currentParams[vid];
            if (update.d === 1) {
                // Heartbeat update: just refresh timestamp
                if (existing) {
                    currentParams[vid] = { ...existing, last_update: now };
                }
            } else {
                // Full or Partial update
                currentParams[vid] = existing ? { ...existing, ...update, last_update: now } : { ...update, last_update: now };
            }
        });

        // TTL Cleanup: Remove vehicles that haven't been updated in 2 seconds
        Object.keys(currentParams).forEach(vid => {
            const v = currentParams[vid];
            if (v && v.last_update && (now - v.last_update > 2000)) {
                delete currentParams[vid];
            }
        });
    }, []);

    const getVehicles = useCallback(() => {
        return Object.values(vehicleStateRef.current);
    }, []);

    return { vehicles: vehicleStateRef, mergeVehicleUpdates, getVehicles };
};
