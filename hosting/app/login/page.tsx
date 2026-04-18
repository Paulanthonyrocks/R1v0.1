"use client";

import React, { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { auth } from '@/lib/firebase';
import { signInWithEmailAndPassword } from 'firebase/auth';
import { FirebaseError } from 'firebase/app';
import MatrixButton from '@/components/MatrixButton';
import { AlertTriangle, Signal, Clock, BatteryFull } from 'lucide-react';

const LoginPage: React.FC = () => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [currentTime, setCurrentTime] = useState<string>('');
  const router = useRouter();

  useEffect(() => {
    const updateTime = () => {
      setCurrentTime(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
    };

    updateTime();
    const timer = setInterval(updateTime, 1000);

    return () => clearInterval(timer);
  }, []);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      if (auth) {
        await signInWithEmailAndPassword(auth, email, password);
        router.push('/dashboard');
      } else {
        console.error("Firebase Auth is not initialized.");
        setError('Firebase Authentication is not available.');
      }
    } catch (err: unknown) {
      if (err instanceof FirebaseError) {
        setError(`Error: ${err.message} (Code: ${err.code})`);
        console.error('Login Error (Firebase):', err.code, err.message);
      } else if (err instanceof Error) {
        setError(`Error: ${err.message}`);
        console.error('Login Error (Generic):', err.message);
      } else {
        setError('An unexpected error occurred.');
        console.error('Login Error (Unknown):', err);
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-lcd-bg text-lcd-text font-lcd flex flex-col min-h-screen w-full">
      {/* Status Bar */}
      <header className="flex items-center justify-between px-4 py-1 border-b-2 border-lcd-text">
        <div className="flex items-center space-x-2">
          <Signal size={20} />
          <span className="font-lcd matrix-glow">LOGIN</span>
        </div>
        <div className="flex items-center space-x-2">
          <Clock size={20} />
          <span className="font-lcd matrix-glow">{currentTime}</span>
          <BatteryFull size={20} />
        </div>
      </header>
      <div className="flex items-center justify-center flex-1">
        <div className="matrix-glow-card p-8 max-w-sm w-full">
          <h1 className="text-2xl font-bold mb-6 text-center uppercase text-lcd-text tracking-normal">Login</h1>
          <form onSubmit={handleLogin}>
            <div className="mb-4">
              <label className="block text-sm font-semibold mb-2 text-lcd-text tracking-normal" htmlFor="email">
                Email
              </label>
              <input
                type="email"
                id="email"
                className="matrix-input w-full tracking-normal placeholder:text-lcd-text"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                placeholder="user@example.com"
              />
            </div>
            <div className="mb-6">
              <label className="block text-sm font-semibold mb-2 text-lcd-text tracking-normal" htmlFor="password">
                Password
              </label>
              <input
                type="password"
                id="password"
                className="matrix-input w-full tracking-normal placeholder:text-lcd-text"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                placeholder="••••••••"
              />
            </div>
            {error && (
              <p className="text-red-500 text-sm mb-4 tracking-normal flex items-center">
                <AlertTriangle className="h-4 w-4 mr-2 flex-shrink-0" />
                {error}
              </p>
            )}
            <MatrixButton
              type="submit"
              className="w-full tracking-normal"
              disabled={loading}
            >
              {loading ? 'Logging In...' : 'Login'}
            </MatrixButton>
          </form>
          <p className="text-center text-sm mt-4 tracking-normal">
            Don&apos;t have an account?{' '}
            <a href="/signup" className="text-lcd-text hover:underline tracking-normal font-lcd">
              Sign up
            </a>
          </p>
        </div>
      </div>
    </div>
  );
};

export default LoginPage;
