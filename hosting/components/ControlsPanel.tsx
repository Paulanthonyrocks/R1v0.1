// frontend/components/ControlsPanel.tsx
"use client";
import React from 'react';
import { useUser } from '@/lib/auth/UserContext';
import { UserRole } from '@/lib/auth/roles';
import AddIncident from './AddIncident';

const ControlsPanel: React.FC = () => {
    const { userRole } = useUser();
    return (
        <footer className="bg-gray-100 p-4 flex items-center justify-between">
            <div>
                {/* Add more controls here in the future */}
            </div>
            {/* Only show the AddIncident button if the user is an OPERATOR or an ADMIN */}
            {(userRole === UserRole.OPERATOR || userRole === UserRole.ADMIN) && (
                <AddIncident />
            )}
        </footer>
    );
};

export default ControlsPanel;