"use client";

import React, { useState } from 'react';
import { useRouter } from 'next/navigation';
import { auth } from '@/lib/firebase';
import { createUserWithEmailAndPassword } from 'firebase/auth';
import { FirebaseError } from 'firebase/app';
import { AlertTriangle } from 'lucide-react'; // Import error icon

const SignupPage: React.FC = () => {
  // Note: MatrixButton component import was missing in the provided file, assuming it should be used like login page.
  // For this diff, I will assume the existing <button className="matrix-button"> is what's intended.
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const router = useRouter();

  const handleSignup = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    if (password !== confirmPassword) {
      setError("Passwords do not match.");
      setLoading(false);
      return;
    }

    try {
      if (auth) {
        await createUserWithEmailAndPassword(auth, email, password);
        // Redirect to dashboard or login page on successful signup
        router.push('/dashboard'); 
      } else {
        console.error("Firebase Auth is not initialized.");
        setError('Firebase Authentication is not available.');
      }
    } catch (err: unknown) {
      if (err instanceof FirebaseError) {
        setError(`Error: ${err.message} (Code: ${err.code})`);
        console.error('Signup Error (Firebase):', err.code, err.message);
      } else if (err instanceof Error) {
        setError(`Error: ${err.message}`);
        console.error('Signup Error (Generic):', err.message);
      } else {
        setError('An unexpected error occurred.');
        console.error('Signup Error (Unknown):', err);
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex items-center justify-center min-h-screen">
      <div className="matrix-glow-card p-8 max-w-sm w-full">
        <h1 className="text-2xl font-bold mb-6 text-center uppercase text-primary tracking-normal">Sign Up</h1> {/* Added tracking-normal */}
        <form onSubmit={handleSignup}>
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
          <div className="mb-4">
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
          <div className="mb-6">
            <label className="block text-sm font-semibold mb-2 text-primary tracking-normal" htmlFor="confirmPassword"> {/* Added text-primary tracking-normal */}
              Confirm Password
            </label>
            <input
              type="password"
              id="confirmPassword"
              className="matrix-input w-full tracking-normal placeholder:text-primary" // Added tracking-normal placeholder:text-primary
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
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
          <button
            type="submit"
            className="matrix-button w-full tracking-normal" /* Added tracking-normal (though .matrix-button base may handle font) */
            disabled={loading}
          >
            {loading ? 'Signing Up...' : 'Sign Up'}
          </button>
        </form>
        <p className="text-center text-sm mt-4 tracking-normal"> {/* Added tracking-normal */}
          Already have an account?{' '}
          <a href="/login" className="text-primary hover:underline tracking-normal"> {/* Added tracking-normal */}
            Login
          </a>
        </p>
      </div>
    </div>
  );
};

export default SignupPage; 