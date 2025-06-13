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
              <Link href="/login" className="block w-full">
                <DropdownMenuItem className="tracking-normal w-full">
                  Login
                </DropdownMenuItem>
              </Link>
            ) : (
              <>
                <Link href="/" className="block w-full">
                  <DropdownMenuItem className="tracking-normal w-full">
                    Home
                  </DropdownMenuItem>
                </Link>
                <Link href="/dashboard" className="block w-full">
                  <DropdownMenuItem className="tracking-normal w-full">
                    Dashboard
                  </DropdownMenuItem>
                </Link>
                <Link href="/anomalies" className="block w-full">
                  <DropdownMenuItem className="tracking-normal w-full">
                    Anomalies
                  </DropdownMenuItem>
                </Link>
                <Link href="/export" className="block w-full">
                  <DropdownMenuItem className="tracking-normal w-full">
                    Export
                  </DropdownMenuItem>
                </Link>
                <Link href="/grid" className="block w-full">
                  <DropdownMenuItem className="tracking-normal w-full">
                    Grid
                  </DropdownMenuItem>
                </Link>
                <Link href="/logs" className="block w-full">
                  <DropdownMenuItem className="tracking-normal w-full">
                    Logs
                  </DropdownMenuItem>
                </Link>
                <Link href="/nodes" className="block w-full">
                  <DropdownMenuItem className="tracking-normal w-full">
                    Nodes
                  </DropdownMenuItem>
                </Link>
                <Link href="/stream" className="block w-full">
                  <DropdownMenuItem className="tracking-normal w-full">
                    Stream
                  </DropdownMenuItem>
                </Link>
                <Link href="/surveillance" className="block w-full">
                  <DropdownMenuItem className="tracking-normal w-full">
                    Surveillance
                  </DropdownMenuItem>
                </Link>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </nav>
  );
}
