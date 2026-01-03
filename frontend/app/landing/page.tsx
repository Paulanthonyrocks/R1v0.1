"use client";

import React, { useRef, useEffect } from 'react';
import HeroSection from '@/components/landing/HeroSection';
import FeaturesSection from '@/components/landing/FeaturesSection';
import SolutionsSection from '@/components/landing/SolutionsSection';
import Link from 'next/link';
import { ChevronLeft } from 'lucide-react';

const LandingPage = () => {
  const scrollRefs = useRef<(HTMLElement | null)[]>([]);

  const addToScrollRefs = (el: HTMLElement | null) => {
    if (el && !scrollRefs.current.includes(el)) {
      scrollRefs.current.push(el);
    }
  };

  useEffect(() => {
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add('visible');
            entry.target.classList.add('animate-in'); // Add a generic animation class if needed
          }
        });
      },
      { threshold: 0.1 }
    );

    scrollRefs.current.forEach((el) => {
      if (el) observer.observe(el);
    });

    return () => {
      scrollRefs.current.forEach((el) => {
        if (el) observer.unobserve(el);
      });
    };
  }, []);

  return (
    <div className="min-h-screen bg-lcd-bg text-lcd-text font-lcd selection:bg-lcd-text selection:text-lcd-bg">
      {/* Back to Home Button */}
      <div className="fixed top-4 left-4 z-50">
        <Link href="/" className="flex items-center gap-2 px-4 py-2 border-2 border-lcd-text bg-lcd-bg hover:bg-lcd-text hover:text-lcd-bg transition-colors font-bold uppercase tracking-widest text-sm">
          <ChevronLeft size={16} /> Main Menu
        </Link>
      </div>

      <main className="flex flex-col">
        <HeroSection />
        
        {/* Divider */}
        <div className="h-4 bg-lcd-text w-full opacity-10"></div>
        
        <FeaturesSection addToScrollRefs={addToScrollRefs} />
        
        {/* Divider */}
        <div className="h-4 bg-lcd-text w-full opacity-10"></div>

        <SolutionsSection addToScrollRefs={addToScrollRefs} />

        <footer className="py-12 border-t-2 border-lcd-text text-center">
            <p className="uppercase tracking-widest text-sm opacity-60">
                &copy; 2026 Route One Systems. All Rights Reserved.
            </p>
        </footer>
      </main>
    </div>
  );
};

export default LandingPage;
