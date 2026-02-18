import React from 'react';
import Link from 'next/link';
import { cn } from '@/lib/utils';
import {
  Network,
  LayoutDashboard,
  Map,
  Bug,
  GitBranch,
  LineChart,
  Eye,
  FileText,
  Database,
  UserCog
} from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip"; // Import Tooltip

interface SidebarProps {
  isCollapsed: boolean;
  className?: string; // Added className prop for flexibility
}

export default function Sidebar({ isCollapsed, className }: SidebarProps) {
  return (
    <TooltipProvider delayDuration={0}> {/* Wrap Sidebar in provider for item tooltips */}
      <aside
        className={cn(
          "flex flex-col bg-card border-r border-border", // Use theme border color
          "h-[100svh] sticky top-0 z-40", // Full height, sticky, below header (z-40)
          // Add default transition here, can be overridden by className
          "transition-all duration-300 ease-in-out",
          isCollapsed ? "w-[70px]" : "w-64", // Dynamic width
          className // Apply external classes
          // Add responsive hiding back when needed: "hidden md:flex"
        )}
        aria-label="Main navigation"
      >
        {/* Logo Area */}
        <div className={cn(
          "p-4 flex items-center border-b border-border", // Use theme border color
           isCollapsed ? "justify-center h-16" : "space-x-3 h-16" // Match header height
        )}>
          <div className="p-1 rounded-lg bg-secondary"> {/* Added bg for contrast */}
            <Network className="text-primary h-6 w-6" /> {/* Use primary color */}
          </div>
          {!isCollapsed && (
            <h1 className="text-xl font-bold matrix-text-glow uppercase tracking-tight">
              Neo<span className="text-muted-foreground font-medium">Traffic</span> {/* Use muted-foreground */}
            </h1>
          )}
        </div>

        {/* Navigation */}
        <nav className="flex-1 overflow-y-auto overflow-x-hidden py-4 px-2"> {/* Hide horizontal overflow */}
          <div className="space-y-1">
            {/* // TODO: Implement active link logic using usePathname */}
            <SidebarItem href="/" icon={LayoutDashboard} isCollapsed={isCollapsed} active>Dashboard</SidebarItem>
            <SidebarItem href="/grid" icon={Map} isCollapsed={isCollapsed}>Traffic Grid</SidebarItem>
            <SidebarItem href="/anomalies" icon={Bug} isCollapsed={isCollapsed} badgeCount={5}>Anomalies</SidebarItem>
            <SidebarItem href="/nodes" icon={GitBranch} isCollapsed={isCollapsed}>Node Control</SidebarItem>
            <SidebarItem href="/stream" icon={LineChart} isCollapsed={isCollapsed}>Data Stream</SidebarItem>
            <SidebarItem href="/surveillance" icon={Eye} isCollapsed={isCollapsed}>Surveillance</SidebarItem>
          </div>

          {/* Reports Section */}
          {!isCollapsed && (
             <h3 className="px-3 mt-6 mb-2 text-xs uppercase text-muted-foreground tracking-normal font-semibold"> {/* Changed tracking-wider to tracking-normal */}
               Reports
             </h3>
          )}
           <div className="space-y-1 mt-2">
             <SidebarItem href="/logs" icon={FileText} isCollapsed={isCollapsed}>System Logs</SidebarItem>
             <SidebarItem href="/export" icon={Database} isCollapsed={isCollapsed}>Data Export</SidebarItem>
           </div>
        </nav>

        {/* User Info */}
        <div className={cn(
          "p-4 border-t border-border mt-auto", // Push to bottom
          isCollapsed ? "flex justify-center" : "flex items-center space-x-3"
        )}>
          <div className={cn(
              "w-8 h-8 rounded-full bg-primary flex items-center justify-center flex-shrink-0", // Use primary for avatar bg
              isCollapsed ? "mx-auto" : ""
          )}>
            <UserCog className="h-5 w-5 text-primary-foreground" /> {/* Use primary-foreground */}
          </div>
          {!isCollapsed && (
            <div className="overflow-hidden">
              <p className="font-medium text-sm truncate text-foreground tracking-normal">Agent Smith</p> {/* Added tracking-normal */}
              <p className="text-xs text-muted-foreground truncate tracking-normal">System Admin</p> {/* Added tracking-normal */}
            </div>
          )}
        </div>
      </aside>
    </TooltipProvider>
  );
}

// Helper component for sidebar items
interface SidebarItemProps {
  href: string;
  icon: React.ElementType;
  children: React.ReactNode;
  isCollapsed: boolean;
  active?: boolean;
  badgeCount?: number;
}

function SidebarItem({ href, icon: Icon, children, isCollapsed, active, badgeCount }: SidebarItemProps) {
  const itemContent = (
     <div // Changed from Link initially to wrap content for tooltip trigger
      className={cn(
        "flex items-center h-9 px-3 py-2 rounded-lg text-sm font-medium cursor-pointer", // Added cursor-pointer
        // Use more subtle text color, highlight primary on hover/active
        "text-muted-foreground hover:text-primary hover:bg-secondary",
        active && "bg-secondary text-primary font-semibold", // Active state style
        isCollapsed ? "justify-center" : "space-x-3"
      )}
    >
      <Icon className={cn("h-5 w-5 flex-shrink-0", isCollapsed ? "mx-auto" : "", active ? "text-primary" : "")} />
      {!isCollapsed && <span className="flex-1 truncate tracking-normal">{children}</span>} {/* Added tracking-normal */}
      {!isCollapsed && badgeCount !== undefined && ( // Check for undefined explicitly
         <Badge variant="default" className="ml-auto bg-primary text-primary-foreground h-5 px-1.5 text-xs font-semibold tracking-normal"> {/* Added tracking-normal to badge text */}
           {badgeCount}
         </Badge>
      )}
    </div>
  );

  return isCollapsed ? (
    <Tooltip delayDuration={0}>
      <TooltipTrigger asChild>
        {/* Wrap the content div in the actual Link */}
        <Link href={href} aria-label={String(children)}>
             {itemContent}
        </Link>
      </TooltipTrigger>
      <TooltipContent side="right" className="flex items-center gap-4">
        <span className="tracking-normal">{String(children)}</span> {/* Added tracking-normal */}
        {badgeCount !== undefined && (
           <Badge variant="secondary" className="ml-auto h-5 px-1.5 text-xs font-semibold tracking-normal"> {/* Added tracking-normal to badge text */}
             {badgeCount}
           </Badge>
        )}
      </TooltipContent>
    </Tooltip>
  ) : (
      // Wrap the content div in the actual Link
     <Link href={href}>
        {itemContent}
     </Link>
  );
}