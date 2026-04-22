import { useState, useEffect, useCallback, useRef } from 'react';
import { WebSocketMessageType } from '../websocket/WebSocketClient';
import { SurveillanceFeedMessage, VideoFrameMessage, VehicleFrontendData } from '../types';

export interface IVehicleRegistry {
    getVehicles(): VehicleFrontendData[];
    updateVehicles(vehicles: VehicleFrontendData[]): void;
    clear(): void;
}

export const useVehicleRegistry = (
    onVehiclesUpdate: (vehicles: VehicleFrontendData[]) => void
) => {
    const vehicleRegistryRef = useRef<Map<string, VehicleFrontendData>>(new Map());
    const lastSeenVehicleTimeRef = useRef<Map<string, number>>(new Map());
    const lastVehiclesUpdateTimeRef = useRef<number>(performance.now());
    const STATE_UPDATE_INTERVAL = 200;

    const clear = useCallback(() => {
        vehicleRegistryRef.current.clear();
        lastSeenVehicleTimeRef.current.clear();
        onVehiclesUpdate([]);
    }, [onVehiclesUpdate]);

    const updateVehicles = useCallback((newVehicles: VehicleFrontendData[]) => {
        const now = performance.now();
        
        // 1. Update registry with new data
        newVehicles.forEach((v) => {
            const vid = v.vehicle_id;
            lastSeenVehicleTimeRef.current.set(vid, now);

            const existing = vehicleRegistryRef.current.get(vid);
            if (existing) {
                const bboxChanged = JSON.stringify(existing.bbox) !== JSON.stringify(v.bbox);
                const updated = { 
                    ...existing, 
                    ...v, 
                    prev_bbox: bboxChanged ? existing.bbox : existing.prev_bbox,
                    last_update_time: bboxChanged ? now : existing.last_update_time
                };
                vehicleRegistryRef.current.set(vid, updated);
            } else {
                const newVehicle = { 
                    ...v, 
                    prev_bbox: v.bbox, 
                    last_update_time: now 
                } as VehicleFrontendData;
                vehicleRegistryRef.current.set(vid, newVehicle);
            }
        });

        // 2. Cleanup stale vehicles
        for (const [vid, lastSeen] of lastSeenVehicleTimeRef.current.entries()) {
            if (now - lastSeen > 2000) {
                vehicleRegistryRef.current.delete(vid);
                lastSeenVehicleTimeRef.current.delete(vid);
            }
        }

        // 3. Throttled UI update
        if (now - lastVehiclesUpdateTimeRef.current > STATE_UPDATE_INTERVAL) {
            onVehiclesUpdate(Array.from(vehicleRegistryRef.current.values()));
            lastVehiclesUpdateTimeRef.current = now;
        }
    }, [onVehiclesUpdate]);

    const getVehicles = useCallback(() => Array.from(vehicleRegistryRef.current.values()), []);

    return {
        updateVehicles,
        getVehicles,
        clear,
        lastVehiclesUpdateTimeRef
    };
};
