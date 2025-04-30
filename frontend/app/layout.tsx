"use client";

import Link from "next/link";
import { useState } from "react";
import { Inter } from "next/font/google";
import { ThemeProvider } from "next-themes";

import "@/styles/globals.css";
import { cn } from "@/lib/utils";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-sans",
});

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body
        className={cn(
          "min-h-screen bg-background antialiased flex flex-col font-matrix text-foreground",
          inter.variable, // Provide fallback sans font variable
        )}
      >
        <ThemeProvider attribute="class">
          <Nav />
          <main className="flex-1 overflow-y-auto p-4 md:p-6 lg:p-8">{children}</main>
        </ThemeProvider>
      </body>
    </html>
  );
}

function Nav() {
  return (
    <nav className="fixed top-0 left-0 w-full z-50 bg-matrix-panel border-b border-matrix-border-color p-4">
      <div className="container mx-auto flex items-center justify-between flex-wrap">
        <h1 className="text-xl font-bold uppercase">Traffic Management Hub</h1>
        <ul className="flex space-x-4">
          <li><Link href="/">Home</Link></li>
          <li><Link href="/anomalies">Anomalies</Link></li>
          <li><Link href="/export">Export</Link></li>
          <li><Link href="/grid">Grid</Link></li>
          <li><Link href="/logs">Logs</Link></li>
          <li><Link href="/nodes">Nodes</Link></li>
          <li><Link href="/stream">Stream</Link></li>
          <li><Link href="/surveillance">Surveillance</Link></li>
        </ul>
      </div>
    </nav>
  );
}
