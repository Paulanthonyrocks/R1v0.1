"use client";

import { usePathname } from "next/navigation";
import { Inter } from "next/font/google";

import { UserProvider } from "@/lib/auth/UserContext"; // Import UserProvider
import Nav from "@/components/layout/Nav"; // Import the new Nav component
import "@/styles/globals.css";
import { cn } from "@/lib/utils";
import { ToastProvider } from "@/components/ui/toast";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-sans",
});

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const pathname = usePathname();
  return (
    <html lang="en" suppressHydrationWarning>
      <UserProvider>
        <head>
          <link rel="icon" href="/logo.png" />
        </head>
        <body
          className={cn(
            "min-h-screen bg-lcd-bg text-lcd-text font-lcd flex flex-col",
            inter.variable,
          )}
        >
          <ToastProvider>
            <main
              className={cn(
                "flex-1 overflow-y-auto relative",
              )}
            >{children}</main>
          </ToastProvider>
        </body>
      </UserProvider>
    </html>
  );
}