'use client';

import React, { createContext, useContext, useState, useCallback } from 'react';

interface VehicleSelectionContextType {
  selectedGlobalId: string | null;
  setSelectedGlobalId: (id: string | null) => void;
  clearSelection: () => void;
}

const VehicleSelectionContext = createContext<VehicleSelectionContextType | undefined>(undefined);

export const VehicleSelectionProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [selectedGlobalId, setSelectedGlobalIdState] = useState<string | null>(null);

  const setSelectedGlobalId = useCallback((id: string | null) => {
    setSelectedGlobalIdState(id);
  }, []);

  const clearSelection = useCallback(() => {
    setSelectedGlobalIdState(null);
  }, []);

  return (
    <VehicleSelectionContext.Provider value={{ selectedGlobalId, setSelectedGlobalId, clearSelection }}>
      {children}
    </VehicleSelectionContext.Provider>
  );
};

export const useVehicleSelection = () => {
  const context = useContext(VehicleSelectionContext);
  if (context === undefined) {
    throw new Error('useVehicleSelection must be used within a VehicleSelectionProvider');
  }
  return context;
};
