"use client";

import { useState } from 'react';
import { Inter } from "next/font/google";
import "@/styles/globals.css";
import { cn } from "@/lib/utils";
import Header from "@/components/layout/header";
import Sidebar from "@/components/layout/sidebar";

const inter = Inter({ subsets: ["latin"], variable: "--font-sans" });

// Reminder: Metadata should be in page.tsx or generateMetadata
// export const metadata = { ... } // <-- Move this out

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);

  const toggleSidebar = () => setIsSidebarCollapsed(prev => !prev);

  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={cn(
            "min-h-screen bg-background font-matrix antialiased flex flex-col",
            inter.variable // Provide fallback sans font variable
        )}
      >
        {/* Pass state and handlers to Header/Sidebar */}
        <Header
            onToggleSidebar={toggleSidebar}
            isSidebarCollapsed={isSidebarCollapsed} // Pass state for aria-expanded
        />
        <div className="flex flex-1 overflow-hidden">
          {/* Apply transition class externally for easier control */}
          <Sidebar
              isCollapsed={isSidebarCollapsed}
              className="transition-all duration-300 ease-in-out" // Example transition
          />
          {/* Consistent padding using Tailwind steps */}
          <main className="flex-1 overflow-y-auto p-4 md:p-6 lg:p-8">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}