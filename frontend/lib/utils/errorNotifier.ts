// frontend/lib/utils/errorNotifier.ts

type NotificationType = 'info' | 'success' | 'warning' | 'error';

interface NotificationOptions {
  duration?: number; // in milliseconds
  closable?: boolean;
}

// This is a placeholder for a real notification system (e.g., a toast library)
// In a real application, you would integrate with a UI library here.
const showNotification = (
  message: string,
  type: NotificationType = 'info',
  options?: NotificationOptions
) => {
  console.log(`[${type.toUpperCase()} Notification]: ${message}`);
  // In a real app, you'd call your toast/notification library here, e.g.:
  // toast[type](message, { duration: options?.duration, closable: options?.closable });
  alert(`[${type.toUpperCase()}]: ${message}`); // Using alert for demonstration
};

export const errorNotifier = {
  info: (message: string, options?: NotificationOptions) =>
    showNotification(message, 'info', options),
  success: (message: string, options?: NotificationOptions) =>
    showNotification(message, 'success', options),
  warning: (message: string, options?: NotificationOptions) =>
    showNotification(message, 'warning', options),
  error: (message: string, options?: NotificationOptions) =>
    showNotification(message, 'error', options),
};
