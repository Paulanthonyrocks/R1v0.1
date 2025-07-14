import React from "react";
import Link from "next/link";
import { AlertTriangle, Signal, Clock, BatteryFull } from 'lucide-react';

export default function UnauthorizedPage() {
  return (
    <div className="bg-lcd-bg text-lcd-text font-lcd flex flex-col min-h-screen w-full">
      {/* Status Bar */}
      <header className="flex items-center justify-between px-4 py-1 border-b-2 border-lcd-text">
        <div className="flex items-center space-x-2">
          <Signal size={20} />
          <span className="font-lcd matrix-glow">UNAUTHORIZED</span>
        </div>
        <div className="flex items-center space-x-2">
          <Clock size={20} />
          <span className="font-lcd matrix-glow">{new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
          <BatteryFull size={20} />
        </div>
      </header>
      <div className="flex flex-col items-center justify-center flex-1">
        <div className="text-center">
          <AlertTriangle className="mx-auto h-16 w-16 text-red-500" />
          <h1 className="mt-4 text-4xl font-bold text-red-500">Unauthorized</h1>
          <p className="mt-2 text-lg">You do not have permission to access this page.</p>
          <Link href="/login" className="mt-6 inline-block px-6 py-2 border-2 border-lcd-text text-lcd-text hover:bg-lcd-text hover:text-lcd-bg transition-colors">
            Return to Login
          </Link>
        </div>
      </div>
    </div>
  );
}
