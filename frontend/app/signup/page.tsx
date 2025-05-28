"use client";

import React, { useState } from 'react';
import { useRouter } from 'next/navigation';
import { auth } from '@/lib/firebase';
import { createUserWithEmailAndPassword } from 'firebase/auth';
import { FirebaseError } from 'firebase/app';

const SignupPage: React.FC = () => {
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
        <h1 className="text-2xl font-bold mb-6 text-center uppercase text-primary">Sign Up</h1>
        <form onSubmit={handleSignup}>
          <div className="mb-4">
            <label className="block text-sm font-semibold mb-2" htmlFor="email">
              Email
            </label>
            <input
              type="email"
              id="email"
              className="matrix-input w-full"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
            />
          </div>
          <div className="mb-4">
            <label className="block text-sm font-semibold mb-2" htmlFor="password">
              Password
            </label>
            <input
              type="password"
              id="password"
              className="matrix-input w-full"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </div>
          <div className="mb-6">
            <label className="block text-sm font-semibold mb-2" htmlFor="confirmPassword">
              Confirm Password
            </label>
            <input
              type="password"
              id="confirmPassword"
              className="matrix-input w-full"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
            />
          </div>
          {error && <p className="text-red-500 text-sm mb-4">{error}</p>}
          <button
            type="submit"
            className="matrix-button w-full"
            disabled={loading}
          >
            {loading ? 'Signing Up...' : 'Sign Up'}
          </button>
        </form>
        <p className="text-center text-sm mt-4">
          Already have an account?{' '}
          <a href="/login" className="text-primary hover:underline">
            Login
          </a>
        </p>
      </div>
    </div>
  );
};

export default SignupPage; 