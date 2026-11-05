// frontend/lib/utils/errorNotifier.ts

type NotificationType = 'info' | 'success' | 'warning' | 'error';

// Bridge to the React toast UI: ToastBridge (mounted in app/layout.tsx) listens
// for this event and renders via useToast. Kept console output for the log stream.
const showNotification = (
  message: string,
  type: NotificationType = 'info'
) => {
  console.log(`[${type.toUpperCase()} Notification]: ${message}`);
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('app:notify', { detail: { message, type } }));
  }
};

export const errorNotifier = {
  info: (message: string) =>
    showNotification(message, 'info'),
  success: (message: string) =>
    showNotification(message, 'success'),
  warning: (message: string) =>
    showNotification(message, 'warning'),
  error: (message: string) =>
    showNotification(message, 'error'),
};