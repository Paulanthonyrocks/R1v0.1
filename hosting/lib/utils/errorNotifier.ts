// frontend/lib/utils/errorNotifier.ts

type NotificationType = 'info' | 'success' | 'warning' | 'error';



// TODO: Integrate with a real notification library (e.g., react-toastify)
// This function is a placeholder for displaying user-facing notifications.
const showNotification = (
  message: string,
  type: NotificationType = 'info'
) => {
  console.log(`[${type.toUpperCase()} Notification]: ${message}`);
  // In a real app, you'd call your toast/notification library here, e.g.:
  // toast[type](message, { duration: 5000, closable: true }); // Example with default options, removed reference to the removed 'options' parameter
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