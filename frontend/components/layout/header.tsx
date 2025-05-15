"use client"

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import React from 'react';
import { Button } from '@/components/ui/button';
import { signOut, Auth } from 'firebase/auth'; // Import Auth type
// Using Panel icons for clearer collapse/expand indication
import { PanelLeftClose, PanelRightOpen, Power } from 'lucide-react'; // Remove unused icons
import { cn } from '@/lib/utils';
import { useUser } from '@/lib/auth/UserContext';
import { UserRole } from '@/lib/auth/roles';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';

import { auth } from '@/lib/firebase'; // Import the auth instance
// Props now include state for ARIA attributes
interface HeaderProps {
  onToggleSidebar: () => void;
  isSidebarCollapsed: boolean; // Added prop
}

export default function Header({ onToggleSidebar, isSidebarCollapsed }: HeaderProps) {
  const { user, userRole, setUserRole } = useUser(); // Get the user object
  const router = useRouter(); // Get the router instance
  return (
    // Use bg-card, apply theme border color via border-border
    <header className={cn(
        "sticky top-0 z-50 w-full h-16 bg-card/80 backdrop-blur-sm flex items-center px-4 md:px-6 justify-between",
        "border-b border-border" // Use theme variable for border
    )}>
      <div className="flex items-center space-x-2 md:space-x-4">
        <Button
          variant="ghost"
          size="icon"
          className="text-primary hover:text-primary/90 hover:bg-secondary" // Use primary color for main interactive element
          onClick={onToggleSidebar}
          // Dynamic aria-label and added aria-expanded
          aria-label={isSidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
          aria-expanded={!isSidebarCollapsed}
        >
          {/* Toggle icon based on state */}
          {isSidebarCollapsed
            ? <PanelRightOpen className="h-5 w-5 md:h-6 md:w-6" />
            : <PanelLeftClose className="h-5 w-5 md:h-6 md:w-6" />
          }
        </Button>
        <h2 className="text-lg md:text-xl font-semibold matrix-text-glow uppercase tracking-wide text-primary">
          Route One
        </h2>
      </div>

      {/* Placeholder action icons */}
      <div className="flex items-center space-x-2">
            <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline" className="flex items-center space-x-2">
                    {userRole}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent>
                  <DropdownMenuItem onClick={() => setUserRole(UserRole.VIEWER)}>
                    {UserRole.VIEWER}
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => setUserRole(UserRole.OPERATOR)}>
                    {UserRole.OPERATOR}
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => setUserRole(UserRole.ADMIN)}>
                    {UserRole.ADMIN}
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>

            <DropdownMenu>
                <DropdownMenuTrigger asChild>
                    {/* Using Button variant outline for better visibility on header */}
                    <Button variant="outline" size="icon" className="text-muted-foreground hover:text-primary hover:bg-secondary" aria-label="More Actions">
                         {/* Consider using a more suitable icon for a menu, e.g., Menu or ThreeDotsVertical */}
                        <PanelRightOpen className="h-4 w-4 md:h-5 md:w-5" />
                    </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                   {/* Navigation Links */}
                  {/* Replaced a tag with Link component for client-side navigation */}
                  <DropdownMenuItem asChild>
 <Link href="/">
 Home</Link>
                  </DropdownMenuItem>
                   <DropdownMenuItem asChild>
                    <a href="/anomalies">Anomalies</a>
                  </DropdownMenuItem>
                   <DropdownMenuItem asChild>
                    <a href="/export">Export</a>
                  </DropdownMenuItem>
                   <DropdownMenuItem asChild>
                    <a href="/grid">Grid</a>
                  </DropdownMenuItem>
                   <DropdownMenuItem asChild>
                    <a href="/logs">Logs</a>
                  </DropdownMenuItem>
                   <DropdownMenuItem asChild>
                    <a href="/nodes">Nodes</a>
 </DropdownMenuItem>
                  {/* Conditionally render Login link */}
 {!user ? (
                    <DropdownMenuItem asChild>
                      <Link href="/login">Login</Link>
                    </DropdownMenuItem>
                  ) : (
                    // Conditionally render Logout link if user is logged in
                    <DropdownMenuItem onClick={async () => {
                      if (auth) { // Check if auth is not null
 await signOut(auth as Auth); // Cast auth to Auth
                      }
                      router.push('/login');
                    }}>
                      Logout
                    </DropdownMenuItem>
                  )}
                </DropdownMenuContent>
              </DropdownMenu>
              <Power className="h-4 w-4 md:h-5 md:w-5" />
      </div>
    </header>
  );
}