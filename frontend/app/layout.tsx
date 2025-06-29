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
            "min-h-screen bg-background antialiased flex flex-col font-lcd text-foreground static-noise",
            inter.variable, // Provide fallback sans font variable
          )}
        >
          <ToastProvider>
            {/* <Nav /> */}
            <main
              className={cn(
                "flex-1 overflow-y-auto relative",
                // pathname !== '/' && 'pt-16' // Apply top padding if not on the homepage (where Nav might be transparent or different)
              )}
            >{children}</main>
          </ToastProvider>
        </body>
      </UserProvider>
    </html>
  );
}
