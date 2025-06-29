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
  const { user, logout } = useUser();
  // const pathname = usePathname(); // Uncomment if Nav uses pathname directly

  return (
    <nav className="fixed top-0 left-0 w-full z-50 matrix-card p-4 rounded-none"> 
      <div className="container mx-auto flex items-center justify-between flex-wrap">
        <Link href="/" className="text-xl font-bold uppercase hover:text-primary transition-colors tracking-normal font-lcd matrix-glow">Route One</Link> {/* Added tracking-normal */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="matrix-button font-lcd matrix-glow"> {/* Added tracking-normal */}
              Menu
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="matrix-card">
            {!user ? (
              <DropdownMenuItem asChild>
                <Link href="/login" className="w-full tracking-normal font-lcd matrix-glow">
                  Login
                </Link>
              </DropdownMenuItem>
            ) : (
              <>
                <DropdownMenuItem asChild>
                  <Link href="/" className="w-full tracking-normal font-lcd matrix-glow">
                    Home
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href="/dashboard" className="w-full tracking-normal font-lcd matrix-glow">
                    Dashboard
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href="/anomalies" className="w-full tracking-normal font-lcd matrix-glow">
                    Anomalies
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href="/export" className="w-full tracking-normal font-lcd matrix-glow">
                    Export
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href="/grid" className="w-full tracking-normal font-lcd matrix-glow">
                    Grid
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href="/logs" className="w-full tracking-normal font-lcd matrix-glow">
                    Logs
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href="/nodes" className="w-full tracking-normal font-lcd matrix-glow">
                    Nodes
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href="/stream" className="w-full tracking-normal font-lcd matrix-glow">
                    Stream
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                  <Link href="/surveillance" className="w-full tracking-normal font-lcd matrix-glow">
                    Surveillance
                  </Link>
                </DropdownMenuItem>
                <DropdownMenuItem onClick={logout} className="w-full tracking-normal cursor-pointer text-red-600 font-lcd matrix-glow">
                  Logout
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </nav>
  );
}
