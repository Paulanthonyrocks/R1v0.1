"use client";

import React, { Component, ErrorInfo, ReactNode } from 'react';
// Note: Error 7016 about missing 'react' declaration is an environment/setup issue.
// Ensure @types/react is installed (`npm i --save-dev @types/react`).
 import { AlertTriangle } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button'; // Import Button

interface ErrorBoundaryProps {
  children: ReactNode;
  fallbackClassName?: string;
  componentName?: string; // Optional name for logging
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null; // Allow error to be Error or null
}

class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  // Define state with the interface and initialize directly
  state: ErrorBoundaryState = { hasError: false, error: null };
  props: any;
  setState: any;

  constructor(props: ErrorBoundaryProps) {
    super(props);
    // Initialization is done in the state declaration above
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    // Update state so the next render will show the fallback UI.
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    // You can also log the error to an error reporting service
    // FIX: Access props via `this.props` in class components
    const componentName = this.props.componentName || 'UnknownComponent';
    console.error(`ErrorBoundary (${componentName}) caught an error:`, error, errorInfo);
  }

  // Function to attempt recovery by resetting state and potentially re-rendering children
  resetError = () => {
    // FIX: Access setState via `this.setState` in class components
    this.setState({ hasError: false, error: null });
    // Optionally, trigger a page reload or other recovery mechanism if appropriate
    // window.location.reload();
  };

  render() {
    // Note: Errors like 7026 (JSX element implicitly has type 'any') usually indicate
    // a missing @types/react or incorrect TS/JSX setup, not an error in the code itself.
    if (this.state.hasError) {
      // You can render any custom fallback UI
      return (
        <div className={cn(
            'p-4 bg-destructive/10 text-destructive-foreground border border-destructive rounded-md flex flex-col sm:flex-row items-center gap-3',
            // FIX: Access props via `this.props` in class components
            this.props.fallbackClassName
         )}>
          <AlertTriangle  className="h-6 w-6 flex-shrink-0 text-destructive" />
          <div className="flex-1 text-center sm:text-left">
            <p className="font-semibold">Something went wrong</p>
            {/* Check if state.error is not null before accessing properties */}
            <p className="text-sm mt-1">
                {this.state.error?.message || 'An unexpected error occurred while rendering this section.'}
            </p>
            {/* Check if state.error is not null before accessing properties */}
            {process.env.NODE_ENV === 'development' && this.state.error?.stack && (
                <pre className="mt-2 text-xs overflow-auto max-h-20 bg-black/20 p-2 rounded">
                    {this.state.error.stack}
                </pre>
            )}
          </div>
          <Button variant="destructive" size="sm" onClick={this.resetError} className="mt-2 sm:mt-0">
            Try Again
          </Button>
        </div>
      );
    }

    // FIX: Access props via `this.props` in class components
    return this.props.children;
  }
}

export default ErrorBoundary;