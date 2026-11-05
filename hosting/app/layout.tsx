'use client';

import '@/styles/globals.css';
import React from 'react';
import { AuthProvider } from '@/lib/auth/AuthProvider';
import { UserProvider } from '@/lib/auth/UserContext';
import { VehicleSelectionProvider } from '@/lib/context/VehicleSelectionContext';
import { WebSocketProvider } from '@/lib/websocket/WebSocketProvider';
import { ToastProvider } from '@/components/ui/toast';
import ToastBridge from '@/components/ToastBridge';
import ErrorBoundary from '@/components/ErrorBoundary';

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
                  <ErrorBoundary>
                  <ToastProvider>
                    <ToastBridge />
                    {children}
                  </ToastProvider>
                  </ErrorBoundary>
                </VehicleSelectionProvider>
              </RealtimeStateProvider>
            </WebSocketProvider>
          </UserProvider>
        </AuthProvider>
      </body>
    </html>
  );
}
