'use client';

import '@/styles/globals.css';
import React from 'react';
import { AuthProvider } from '@/lib/auth/AuthProvider';
import { UserProvider } from '@/lib/auth/UserContext';
import { VehicleSelectionProvider } from '@/lib/context/VehicleSelectionContext';
import { WebSocketProvider } from '@/lib/websocket/WebSocketProvider';

import { RealtimeStateProvider } from '@/lib/context/RealtimeStateContext';

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <AuthProvider>
          <UserProvider>
            <WebSocketProvider>
              <RealtimeStateProvider>
                <VehicleSelectionProvider>
                  {children}
                </VehicleSelectionProvider>
              </RealtimeStateProvider>
            </WebSocketProvider>
          </UserProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
