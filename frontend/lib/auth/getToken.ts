import { auth } from '../firebase';

export const getToken = async () => {
  if (!auth) {
    console.warn("Firebase Auth is not initialized.");
    return null;
  }
  const user = auth.currentUser;
  if (user) {
    const token = await user.getIdToken();
    return token;
  }
  return null;
};