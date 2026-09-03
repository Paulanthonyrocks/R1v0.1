"use client";

import React, { useState, useEffect } from 'react';
import Link from 'next/link';
import {
  Map,
  Book,
  AlertTriangle,
  BarChart2,
  Settings,
  Signal,
  BatteryFull,
  Clock,
  LayoutDashboard,
  Info,
  CloudSun
} from 'lucide-react';
import { getAuth, signOut } from 'firebase/auth';
import RetroModal from '@/components/ui/RetroModal';

// Mock data for menu items
const menuItems = [
  { href: '/dashboard', label: 'DASHBOARD', icon: <LayoutDashboard size={48} /> },
  { href: '/dashboard/map', label: 'LIVE MAP', icon: <Map size={48} /> },
  { href: '/signals', label: 'SIGNALS', icon: <Signal size={48} /> },
  { href: '/impacts', label: 'IMPACTS', icon: <CloudSun size={48} /> },
  { href: '/logs', label: 'SYSTEM LOGS', icon: <Book size={48} /> },
  { href: '/anomalies', label: 'ALERTS', icon: <AlertTriangle size={48} /> },
  { href: '/dashboard/analytics', label: 'ANALYTICS', icon: <BarChart2 size={48} /> },
  { href: '/landing', label: 'ABOUT', icon: <Info size={48} /> },
];

const NokiaHomeScreen = () => {
  const [booting, setBooting] = useState(true);
  const [time, setTime] = useState("--:--");
  const [activeIndex, setActiveIndex] = useState(0);
  const [isModalOpen, setIsModalOpen] = useState(false);

  const handleLogout = async () => {
    const auth = getAuth();
    try {
        await signOut(auth);
        // Redirect to login page or home page after logout
        window.location.href = '/login'; // Or '/' depending on your app's flow
    } catch (error) {
        console.error("Error logging out:", error);
        // Optionally, show an error message to the user
    }
  };

  useEffect(() => {
    const handleFirstInteraction = () => {
      window.removeEventListener('click', handleFirstInteraction);
      window.removeEventListener('keydown', handleFirstInteraction);
    };

    window.addEventListener('click', handleFirstInteraction);
    window.addEventListener('keydown', handleFirstInteraction);

    setTimeout(() => {
      setBooting(false);
    }, 2500);

    const timer = setInterval(() => {
      setTime(new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }));
    }, 1000);

    return () => {
      clearInterval(timer);
      window.removeEventListener('click', handleFirstInteraction);
      window.removeEventListener('keydown', handleFirstInteraction);
    };
  }, []);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (booting) return;

      let newIndex = activeIndex;
      if (e.key === 'ArrowRight') {
        newIndex = (activeIndex + 1) % menuItems.length;
      } else if (e.key === 'ArrowLeft') {
        newIndex = (activeIndex - 1 + menuItems.length) % menuItems.length;
      } else if (e.key === 'Enter') {
        // Navigate to the selected item
        window.location.href = menuItems[activeIndex].href;
      }

      if (newIndex !== activeIndex) {
        setActiveIndex(newIndex);
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [activeIndex, booting]);

  if (booting) {
    return (
      <div className="bg-lcd-bg text-lcd-text font-lcd flex flex-col h-screen w-full items-center justify-center">
        <div className="animate-pulse">
          <h1 className="text-6xl font-bold tracking-widest">ROUTE ONE</h1>
          <p className="text-center text-xl">BOOTING...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="bg-lcd-bg text-lcd-text font-lcd flex flex-col h-screen w-full">
      {/* Status Bar */}
      <header className="flex items-center justify-between px-4 py-1 border-b-2 border-lcd-text">
        <div className="flex items-center space-x-2">
          <Signal size={20} />
          <span>AI-TMS</span>
        </div>
        <div className="flex items-center space-x-2">
          <Clock size={20} />
          <span>{time}</span>
          <BatteryFull size={20} />
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 flex items-center justify-center p-4">
        <div className="w-full max-w-md">
          <div className="text-center mb-8">
            <h1 className="text-4xl font-bold tracking-widest">ROUTE ONE</h1>
          </div>
          <div className="grid grid-cols-4 gap-4">
            {menuItems.map((item, index) => (
              <Link
                href={item.href}
                key={item.href}
                className={`flex flex-col items-center justify-center p-4 border-2 border-lcd-text transition-colors duration-200 menu-item-hover-glow ${
                  index === activeIndex ? 'bg-lcd-text text-lcd-bg menu-item-active' : ''
                }`}
                onMouseEnter={() => {
                  setActiveIndex(index);
                }}
              >
                {item.icon}
                <span className="mt-2 text-sm font-bold">{item.label}</span>
              </Link>
            ))}
          </div>
        </div>
      </main>

      {/* Soft Keys */}
      <footer className="flex justify-between px-4 py-2 border-t-2 border-lcd-text">
        <button className="font-bold text-lg">MENU</button>
        <button onClick={() => setIsModalOpen(true)} className="font-bold text-lg">OPTIONS</button>
      </footer>

      <RetroModal isOpen={isModalOpen} onClose={() => setIsModalOpen(false)} title="OPTIONS">
        <div className="space-y-4">
          <div className="flex justify-between items-center">
            <label>SOUND FX</label>
            <input type="checkbox" defaultChecked />
          </div>
          <div className="flex justify-between items-center">
            <label>STARTUP JINGLE</label>
            <input type="checkbox" defaultChecked />
          </div>
          <div className="flex justify-between items-center">
            <label>SCREEN FLICKER</label>
            <input type="checkbox" defaultChecked />
          </div>
          <button onClick={handleLogout} className="w-full p-2 border-2 border-lcd-text text-lcd-text font-bold text-lg menu-item-hover-glow">LOGOUT</button>
        </div>
      </RetroModal>
    </div>
  );
};

export default NokiaHomeScreen;