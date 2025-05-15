"use client";

import { initializeApp, getApps, getApp, FirebaseApp } from 'firebase/app';
import { Auth, getAuth } from 'firebase/auth';
import { Firestore, getFirestore } from 'firebase/firestore';

// Debug logging for environment variables
console.log('Environment Variables Check:', {
  NEXT_PUBLIC_FIREBASE_API_KEY: process.env.NEXT_PUBLIC_FIREBASE_API_KEY || 'missing',
  NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN || 'missing',
  NODE_ENV: process.env.NODE_ENV || 'missing'
});

const firebaseConfig = {
  apiKey: process.env.NEXT_PUBLIC_FIREBASE_API_KEY,
  authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN,
  projectId: process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID,
  storageBucket: process.env.NEXT_PUBLIC_FIREBASE_STORAGE_BUCKET,
  messagingSenderId: process.env.NEXT_PUBLIC_FIREBASE_MESSAGING_SENDER_ID,
  appId: process.env.NEXT_PUBLIC_FIREBASE_APP_ID,
  measurementId: process.env.NEXT_PUBLIC_FIREBASE_MEASUREMENT_ID,
};

// Debug logging
console.log('Firebase config:', {
  apiKey: firebaseConfig.apiKey ? '**exists**' : '**missing**',
  authDomain: firebaseConfig.authDomain ? '**exists**' : '**missing**',
  projectId: firebaseConfig.projectId ? '**exists**' : '**missing**',
});

let app: FirebaseApp;
try {
  if (!getApps().length) {
    if (!firebaseConfig.apiKey) {
      throw new Error('Firebase API key is missing. Please check your environment variables.');
    }
    app = initializeApp(firebaseConfig);
  } else {
    app = getApp();
  }
} catch (error) {
  console.error('Error initializing Firebase:', error);
  throw error;
}

// Check if app is not null before getting Firestore and Auth instances
let db: Firestore | null = null;
let auth: Auth | null = null;

if (app) {
  try {
    db = getFirestore(app);
    auth = getAuth(app);
  } catch (error) {
    console.error('Error initializing Firebase services:', error);
    throw error;
  }
}

export { app, db, auth };