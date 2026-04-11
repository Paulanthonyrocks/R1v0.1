'use client';

import '@/styles/globals.css';
import React from 'react';
import { AuthProvider } from '@/lib/auth/AuthProvider';
import { UserProvider } from '@/lib/auth/UserContext';
import { VehicleSelectionProvider } from '@/lib/context/VehicleSelectionContext';
import { WebSocketProvider } from '@/lib/websocket/WebSocketProvider';

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>
          <UserProvider>
            <WebSocketProvider>
              <VehicleSelectionProvider>
                {children}
              </VehicleSelectionProvider>
            </WebSocketProvider>
          </UserProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
