"use client"; // Keep this if it was in the original Nav

import Link from "next/link";
import { useUser } from "@/lib/auth/UserContext";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

// Potentially add other imports if Nav depends on them, e.g., specific icons

export default function Nav() {
  const { user } = useUser();
  // const pathname = usePathname(); // Uncomment if Nav uses pathname directly

  return (
    <nav className="fixed top-0 left-0 w-full z-50 glossy-gradient p-4 rounded-lg border-b border-primary">
      <div className="container mx-auto flex items-center justify-between flex-wrap">
        <Link href="/" className="text-xl font-bold uppercase hover:text-primary transition-colors tracking-normal">Route One</Link> {/* Added tracking-normal */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="text-foreground hover:text-primary focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-1 focus:ring-offset-background rounded tracking-normal"> {/* Added tracking-normal */}
              Menu
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {!user ? (
              <DropdownMenuItem asChild>
                <Link href="/login" className="w-full tracking-normal">
                  Login
                </Link>
              </DropdownMenuItem>
            ) : (
              <>
                <DropdownMenuItem asChild>
                  <Link href="/" className="w-full tracking-normal">
                    Home
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href="/dashboard" className="w-full tracking-normal">
                    Dashboard
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href="/anomalies" className="w-full tracking-normal">
                    Anomalies
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href="/export" className="w-full tracking-normal">
                    Export
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href="/grid" className="w-full tracking-normal">
                    Grid
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href="/logs" className="w-full tracking-normal">
                    Logs
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href="/nodes" className="w-full tracking-normal">
                    Nodes
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href="/stream" className="w-full tracking-normal">
                    Stream
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href="/surveillance" className="w-full tracking-normal">
                    Surveillance
                  </Link>
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </nav>
  );
}
