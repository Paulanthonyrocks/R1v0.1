"use client"

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import React from 'react';
import { Button } from '@/components/ui/button';
import { signOut, Auth } from 'firebase/auth'; // Import Auth type
// Using Panel icons for clearer collapse/expand indication
import { PanelLeftClose, PanelRightOpen, Power, User } from 'lucide-react'; // Remove unused icons
import { cn } from '@/lib/utils';
import { useUser } from '@/lib/auth/UserContext';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';

import { auth } from '@/lib/firebase'; // Import the auth instance
// Props now include state for ARIA attributes
interface HeaderProps {
  onToggleSidebar: () => void;
  isSidebarCollapsed: boolean; // Added prop
}

export default function Header({ onToggleSidebar, isSidebarCollapsed }: HeaderProps) {
  const { user } = useUser(); // Get the user object
  const router = useRouter(); // Get the router instance
  return (
    // Use bg-card, apply theme border color via border-border
    <header className={cn("sticky top-0 z-50 w-full h-16 bg-card/80 backdrop-blur-sm flex items-center px-4 md:px-6 justify-between"
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
        <h2 className="text-lg md:text-xl font-semibold matrix-text-glow uppercase tracking-normal text-primary"> {/* Changed tracking-wide to tracking-normal */}
          Route One
        </h2>
      </div>

      {/* Placeholder action icons */}
      <div className="flex items-center space-x-2">

        {user ? (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" className="text-primary hover:text-primary/90 hover:bg-secondary">
                <User className="h-5 w-5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={async () => {
                if (auth) {
                  await signOut(auth as Auth);
                }
                router.push('/login');
              }}>
                Logout
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ) : (
          <Link href="/login">
            <Button variant="ghost" className="text-primary hover:text-primary/90 hover:bg-secondary">
              Login
            </Button>
          </Link>
        )}
        <Power className="h-4 w-4 md:h-5 md:w-5 text-primary" /> {/* Added text-primary */}
      </div>
    </header>
  );
}