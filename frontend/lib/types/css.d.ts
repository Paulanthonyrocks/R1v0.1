// frontend/lib/types/css.d.ts
import 'react';

declare module 'react' {
  interface CSSProperties {
    msImageRendering?: string;
    mozImageRendering?: string;
    // Add other vendor-specific properties here if needed in the future
    // For example, webkit specific properties if they are not already covered
    // WebkitAppearance?: string;
    // MozAppearance?: string;
    // etc.
  }
}
