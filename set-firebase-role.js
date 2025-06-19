// Usage: node set-firebase-role.js <UID> <role>
// Example: node set-firebase-role.js some-uid admin

const admin = require('firebase-admin');

// Path to your Firebase service account key JSON file
const serviceAccount = require('./backend/configs/firebase/service-account-key.json');

admin.initializeApp({
  credential: admin.credential.cert(serviceAccount),
});

const [,, uid, role] = process.argv;

if (!uid || !role) {
  console.error('Usage: node set-firebase-role.js <UID> <role>');
  process.exit(1);
}

admin.auth().setCustomUserClaims(uid, { role })
  .then(() => {
    console.log(`Custom claim 'role: ${role}' set for user ${uid}`);
    process.exit(0);
  })
  .catch((error) => {
    console.error('Error setting custom claim:', error);
    process.exit(1);
  });
