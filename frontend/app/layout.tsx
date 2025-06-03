"use client";

import { usePathname } from "next/navigation";
import { Inter } from "next/font/google";
import { ThemeProvider } from "next-themes";

import { UserProvider } from "@/lib/auth/UserContext"; // Import UserProvider
import Nav from "@/components/layout/Nav"; // Import the new Nav component
import "@/styles/globals.css";
import { cn } from "@/lib/utils";

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
        <body
          className={cn(
            "min-h-screen bg-background antialiased flex flex-col font-matrix text-foreground",
            inter.variable, // Provide fallback sans font variable
          )}
        >
          <ThemeProvider attribute="class">
            <Nav />
            <main
              className={cn("flex-1 overflow-y-auto p-4 md:p-6 lg:p-8", pathname !== '/' && 'pt-16')}
            >{children}</main>
          </ThemeProvider>
        </body>
      </UserProvider>
    </html>
  );
}
