// Firebase Cloud Function: Set default role for new users
// Place this in your functions directory and deploy with Firebase CLI

const functions = require('firebase-functions');
const admin = require('firebase-admin');

admin.initializeApp();

// This function triggers when a new user is created
exports.setDefaultUserRole = functions.auth.user().onCreate(async (user) => {
  // Set the default role (e.g., 'viewer')
  const defaultRole = 'viewer';
  try {
    await admin.auth().setCustomUserClaims(user.uid, { role: defaultRole });
    console.log(`Default role '${defaultRole}' set for user ${user.uid}`);
  } catch (error) {
    console.error('Error setting default role:', error);
  }
});
