"use client";

// Bridges errorNotifier's window events into the toast UI. Mount once inside
// ToastProvider (app/layout.tsx) so lib code (WebSocketClient, APIClient) can
// raise visible notifications without importing React.
import React, { useEffect } from 'react';
import { useToast } from '@/components/ui/toast';

type NotifyDetail = { message: string; type: 'info' | 'success' | 'warning' | 'error' };

const ToastBridge: React.FC = () => {
  const { showToast } = useToast();

  useEffect(() => {
    const handler = (e: Event) => {
      const { message, type } = (e as CustomEvent<NotifyDetail>).detail;
      showToast(message, type);
    };
    window.addEventListener('app:notify', handler);
    return () => window.removeEventListener('app:notify', handler);
  }, [showToast]);

  return null;
};

export default ToastBridge;
