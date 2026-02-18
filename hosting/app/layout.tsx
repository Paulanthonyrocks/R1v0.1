"use client";

// import { Inter } from "next/font/google"; // Commented out due to Google Fonts download issues

import { UserProvider } from "@/lib/auth/UserContext"; // Import UserProvider
import "@/styles/globals.css";
import { cn } from "@/lib/utils";
import { ToastProvider } from "@/components/ui/toast";
import { WebSocketProvider } from "@/lib/websocket/WebSocketProvider";

// const inter = Inter({ // Commented out due to Google Fonts download issues
//   subsets: ["latin"],
//   variable: "--font-sans",
// });

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  
  return (
    <html lang="en" suppressHydrationWarning>
      <UserProvider>
        <WebSocketProvider>
          <head>
            <link rel="icon" href="/logo.png" />
          </head>
          <body
            className={cn(
              "min-h-screen bg-lcd-bg text-lcd-text font-lcd flex flex-col",
              // inter.variable, // Commented out due to Google Fonts download issues
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
        </WebSocketProvider>
      </UserProvider>
    </html>
  );
}