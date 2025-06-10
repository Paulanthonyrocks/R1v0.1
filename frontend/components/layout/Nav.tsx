"use client"; // Keep this if it was in the original Nav

import Link from "next/link";
import { usePathname } from "next/navigation"; // Keep if used, or remove
import { useUser } from "@/lib/auth/UserContext";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils"; // Keep if used by Nav for class names

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
              <DropdownMenuItem className="tracking-normal" asChild> {/* Added tracking-normal */}
                <Link href="/login">Login</Link>
              </DropdownMenuItem>
            ) : (
              <>
                <DropdownMenuItem className="tracking-normal" asChild> {/* Added tracking-normal */}
                  <Link href="/">Home</Link>
                </DropdownMenuItem>
                <DropdownMenuItem className="tracking-normal" asChild> {/* Added tracking-normal */}
                  <Link href="/dashboard">Dashboard</Link>
                </DropdownMenuItem>
                <DropdownMenuItem className="tracking-normal" asChild> {/* Added tracking-normal */}
                  <Link href="/anomalies">Anomalies</Link>
                </DropdownMenuItem>
                <DropdownMenuItem className="tracking-normal" asChild> {/* Added tracking-normal */}
                  <Link href="/export">Export</Link>
                </DropdownMenuItem>
                <DropdownMenuItem className="tracking-normal" asChild> {/* Added tracking-normal */}
                  <Link href="/grid">Grid</Link>
                </DropdownMenuItem>
                <DropdownMenuItem className="tracking-normal" asChild> {/* Added tracking-normal */}
                  <Link href="/logs">Logs</Link>
                </DropdownMenuItem>
                <DropdownMenuItem className="tracking-normal" asChild> {/* Added tracking-normal */}
                  <Link href="/nodes">Nodes</Link>
                </DropdownMenuItem>
                <DropdownMenuItem className="tracking-normal" asChild> {/* Added tracking-normal */}
                  <Link href="/stream">Stream</Link>
                </DropdownMenuItem>
                <DropdownMenuItem className="tracking-normal" asChild> {/* Added tracking-normal */}
                  <Link href="/surveillance">Surveillance</Link>
                </DropdownMenuItem>
              </>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </nav>
  );
}
