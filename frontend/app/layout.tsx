"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Inter } from "next/font/google";
import { ThemeProvider } from "next-themes";

import { useUser, UserProvider } from "@/lib/auth/UserContext"; // Import UserProvider and useUser
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
      <UserProvider> {/* Wrap content with UserProvider */}
      <body
        className={cn(
          "min-h-screen bg-background antialiased flex flex-col font-matrix text-foreground",
          inter.variable, // Provide fallback sans font variable

        )}
      >
        <ThemeProvider attribute="class">
          <Nav />
          <main
            className={cn("flex-1 overflow-y-auto p-4 md:p-6 lg:p-8")}
            style={{ paddingTop: pathname !== '/' ? '4rem' : undefined }}
          >{children}</main>
        </ThemeProvider> {/* Close ThemeProvider */}
      </body>
      </UserProvider> {/* Close UserProvider */}
    </html>
  );
}

function Nav() {
  const { user } = useUser(); // Use the useUser hook

  if (!user) {
    return null; // Only render Nav if user is authenticated
  }
  return (
    <nav className="fixed top-0 left-0 w-full z-50 glossy-gradient p-4 rounded-lg">
      <div className="container mx-auto flex items-center justify-between flex-wrap">
        <h1 className="text-xl font-bold uppercase">Route One</h1>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            {/* You might want a button here for better accessibility and styling */}
            <button className="text-foreground hover:text-primary focus:outline-none">
              Menu
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem asChild>
              <Link href="/">Home</Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link href="/anomalies">Anomalies</Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link href="/export">Export</Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link href="/grid">Grid</Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link href="/logs">Logs</Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link href="/nodes">Nodes</Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild>
              <Link href="/stream">Stream</Link>
            </DropdownMenuItem>
            <DropdownMenuItem asChild><Link href="/surveillance">Surveillance</Link></DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </nav>
  );
}
