import React from 'react';
import { ArrowDown } from 'lucide-react';
import Link from 'next/link';

const HeroSection: React.FC = () => {
  return (
    <section className="min-h-screen flex flex-col items-center justify-center text-center p-4 relative overflow-hidden">
      {/* Background decoration - maybe a subtle grid or scanlines handled by global CSS, 
          but we can add a specific graphic here */}
      
      <div className="z-10 space-y-6 max-w-4xl">
        <h1 className="text-6xl md:text-8xl font-bold tracking-widest font-lcd matrix-glow mb-4">
          ROUTE ONE
        </h1>
        <p className="text-xl md:text-2xl font-lcd tracking-wider opacity-80 uppercase">
          Next-Gen Traffic Control <br /> 
          <span className="text-sm opacity-60">Powered by 8-bit Intelligence</span>
        </p>
        
        <div className="pt-8 flex flex-col sm:flex-row gap-4 justify-center items-center">
          <Link href="/dashboard" className="px-8 py-3 border-2 border-lcd-text bg-lcd-text text-lcd-bg font-bold hover:bg-transparent hover:text-lcd-text hover:shadow-[0_0_10px_var(--lcd-text)] transition-all uppercase tracking-widest text-lg">
            Launch System
          </Link>
          <Link href="#features" className="px-8 py-3 border-2 border-lcd-text text-lcd-text font-bold hover:bg-lcd-text hover:text-lcd-bg transition-all uppercase tracking-widest text-lg">
            Learn More
          </Link>
        </div>
      </div>

      <div className="absolute bottom-10 animate-bounce">
        <ArrowDown size={32} />
      </div>
    </section>
  );
};

export default HeroSection;
