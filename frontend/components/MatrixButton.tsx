// frontend/components/MatrixButton.tsx
import React, { ButtonHTMLAttributes } from 'react';
import { cn } from '@/lib/utils'; // Ensure this path is correct

interface MatrixButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  size?: 'small' | 'medium' | 'large';
  className?: string;
  // Consider adding a 'variant' prop for different styles like 'outline', 'destructive' in the future
}

const MatrixButton: React.FC<MatrixButtonProps> = ({
  children,
  className,
  size = 'medium',
  disabled,
  ...props
}) => {
  const sizeClasses = {
    small: 'px-2 py-1 text-xs',         // Original: padding = '0.2rem 0.5rem'; fontSize = '0.8rem';
    medium: 'px-4 py-2 text-sm',        // Original: padding = '0.5rem 1rem'; fontSize = '1rem'; (adjusted to common Tailwind text-sm)
    large: 'px-6 py-3 text-base',       // Original: padding = '0.8rem 1.5rem'; fontSize = '1.2rem'; (adjusted to common Tailwind text-base)
  };

  // Base styles inspired by original inline styles and typical button requirements
  // Also aligning with .matrix-button from globals.css where it makes sense (e.g., font, uppercase)
  const baseStyle = cn(
    "font-matrix", // Original: fontFamily: 'var(--font-mono)' -> Tailwind equivalent is font-matrix if defined
    "uppercase",
    "font-light", // Original: fontWeight: '300'
    "rounded",    // Original: borderRadius: '4px' -> Tailwind 'rounded' is 0.25rem (4px)
    "border",
    "transition-colors duration-200", // Original: transition: 'background-color 0.2s, border-color 0.2s',
    "focus:outline-none",
    "disabled:cursor-not-allowed"
  );

  // Default "Matrix" theme variant (Green button)
  // Matches original defaults: backgroundColor = 'hsl(var(--matrix))', textColor = 'hsl(var(--matrix-bg))'
  // Hover: backgroundColor = `hsl(var(--matrix-light))`, borderColor = `hsl(var(--matrix-light))`
  // Focus: boxShadow = `0 0 0 2px hsl(var(--matrix-light))`
  // Disabled: backgroundColor = `hsl(var(--matrix-muted-text))`, borderColor = `hsl(var(--matrix-muted-text))`, color = `hsl(var(--matrix-dark))`
  const primaryMatrixVariant = cn(
    "bg-primary border-primary text-primary-foreground", // Default state
    "hover:bg-primary/90", // Updated hover state, border remains border-primary
    "focus:ring-2 focus:ring-offset-2 focus:ring-ring focus:ring-offset-background", // Focus state - using theme ring color
    "disabled:bg-muted disabled:border-muted disabled:text-muted-foreground" // Disabled state - using theme's muted colors
  );
  // Note: text-primary-foreground is often dark (like matrix-bg). matrix-light is a lighter green.
  // If hover text should also change, add `hover:text-some-other-color`. Original JS didn't change text color on hover.

  return (
    <button
      {...props}
      disabled={disabled}
      className={cn(
        baseStyle,
        primaryMatrixVariant, // Apply the default variant
        sizeClasses[size],
        className // Allows overriding via className prop
      )}
    >
      {children}
    </button>
  );
};

export default MatrixButton;