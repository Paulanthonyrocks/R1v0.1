"use client";

import React, { useState } from 'react';
import { useRouter } from 'next/navigation';
import { auth } from '@/lib/firebase'; // Assuming you have a firebase.ts file exporting 'auth'
// Auth and signInWithEmailAndPassword are correctly imported from 'firebase/auth'
import { signInWithEmailAndPassword, } from 'firebase/auth';
// FirebaseError should be imported from 'firebase/app' - Note: FirebaseError is typically imported from 'firebase/auth' or 'firebase/app'. Let's check the actual export location.
import { FirebaseError } from 'firebase/app';
import MatrixButton from '@/components/MatrixButton'; // Add this import
import { AlertTriangle } from 'lucide-react'; // Import error icon

const LoginPage: React.FC = () => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      if (auth) { // Check if auth is not null
        // If auth is properly typed as Auth (or Auth | null) in firebase.ts,
        // the 'auth as Auth' cast is not strictly necessary here due to the if (auth) check.
        // However, keeping it doesn't harm if you want to be explicit.
        // For simplicity, if `auth` is indeed an `Auth` instance, the cast can be removed.
        await signInWithEmailAndPassword(auth, email, password);
        // Redirect to dashboard on successful login
        router.push('/dashboard');
      } else {
        console.error("Firebase Auth is not initialized.");
        // Optionally, display an error message to the user
        setError('Firebase Authentication is not available.');
      }
    } catch (err: unknown) {
      // Handle Firebase specific errors or general Errors
      if (err instanceof FirebaseError) {
        // Access FirebaseError specific properties
        // err is now correctly typed as FirebaseError within this block
        setError(`Error: ${err.message} (Code: ${err.code})`);
        console.error('Login Error (Firebase):', err.code, err.message);
      } else if (err instanceof Error) {
        // Handle generic Error instances
        // err is now correctly typed as Error within this block
        setError(`Error: ${err.message}`);
        console.error('Login Error (Generic):', err.message);
      } else {
        setError('An unexpected error occurred.');
        console.error('Login Error (Unknown):', err); // Log the unknown error for debugging
      }
    } finally { // Correctly placed finally block
      setLoading(false);
    }
  };

  return (
    <div className="flex items-center justify-center min-h-screen">
      <div className="matrix-glow-card p-8 max-w-sm w-full"> {/* Using matrix-glow-card for styling */}
        <h1 className="text-2xl font-bold mb-6 text-center uppercase text-primary tracking-normal">Login</h1> {/* Added tracking-normal */}
        <form onSubmit={handleLogin}>
          <div className="mb-4">
            <label className="block text-sm font-semibold mb-2 text-primary tracking-normal" htmlFor="email"> {/* Added text-primary tracking-normal */}
              Email
            </label>
            <input
              type="email"
              id="email"
              className="matrix-input w-full tracking-normal placeholder:text-primary" // Added tracking-normal placeholder:text-primary
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              placeholder="user@example.com" // Added placeholder example
            />
          </div>
          <div className="mb-6">
            <label className="block text-sm font-semibold mb-2 text-primary tracking-normal" htmlFor="password"> {/* Added text-primary tracking-normal */}
              Password
            </label>
            <input
              type="password"
              id="password"
              className="matrix-input w-full tracking-normal placeholder:text-primary" // Added tracking-normal placeholder:text-primary
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
              placeholder="••••••••" // Added placeholder example
            />
          </div>
          {error && (
            <p className="text-primary text-sm mb-4 tracking-normal flex items-center">
              <AlertTriangle className="h-4 w-4 mr-2 flex-shrink-0" /> {/* Icon added, color inherited (text-primary) */}
              {error}
            </p>
          )}
          <MatrixButton
            type="submit"
            className="w-full tracking-normal" /* tracking-normal might be inherent from MatrixButton base styles but explicit here */
            disabled={loading}
          >
            {loading ? 'Logging In...' : 'Login'}
          </MatrixButton>
        </form>
        <p className="text-center text-sm mt-4 tracking-normal"> {/* Added tracking-normal */}
          Don&apos;t have an account?{' '}
          <a href="/signup" className="text-primary hover:underline tracking-normal"> {/* Added tracking-normal */}
            Sign up
          </a>
        </p>
      </div>
    </div>
  );
};

export default LoginPage;