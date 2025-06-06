/** @type {import('tailwindcss').Config} */
import tailwindcssAnimate from 'tailwindcss-animate'

const config = {
    darkMode: ["class"],
    content: [
        './pages/**/*.{ts,tsx}',
        './components/**/*.{ts,tsx}',
        './app/**/*.{ts,tsx}',
        './src/**/*.{ts,tsx}',
        './data/**/*.{ts,tsx}',
        './lib/**/*.{ts,tsx}',
    ],
    prefix: "",
    theme: {
        container: { center: true, padding: "2rem", screens: { "2xl": "1400px" } },
        extend: {
            fontFamily: {
                matrix: ['var(--font-matrix)', 'monospace'],
                // sans: ['var(--font-sans)', /* ... */ ],
            },
            colors: {
                border: "hsl(var(--border))",
                input: "hsl(var(--input))",
                ring: "hsl(var(--ring))",
                background: "hsl(var(--background))",
                foreground: "hsl(var(--foreground))",
                primary: {
                    DEFAULT: "hsl(var(--primary))",
                    foreground: "hsl(var(--primary-foreground))",
                },
                secondary: {
                    DEFAULT: "hsl(var(--secondary))",
                    foreground: "hsl(var(--secondary-foreground))",
                },
                destructive: {
                    DEFAULT: "hsl(var(--destructive))",
                    foreground: "hsl(var(--destructive-foreground))",
                },
                muted: {
                    DEFAULT: "hsl(var(--muted))",
                    foreground: "hsl(var(--muted-foreground))",
                },
                accent: {
                    DEFAULT: "hsl(var(--accent))",
                    foreground: "hsl(var(--accent-foreground))",
                },
                popover: {
                    DEFAULT: "hsl(var(--popover))",
                    foreground: "hsl(var(--popover-foreground))",
                },
                card: {
                    DEFAULT: "hsl(var(--card))",
                    foreground: "hsl(var(--card-foreground))",
                },
                'matrix': {
                    DEFAULT: 'hsl(var(--matrix))',
                    light: 'hsl(var(--matrix-light))',
                    dark: 'hsl(var(--matrix-dark))',
                },
                warning: { // New semantic color
                  DEFAULT: "hsl(var(--warning))",
                  foreground: "hsl(var(--warning-foreground))",
                },
                'accent-anomaly': { // New semantic color with hyphen
                  DEFAULT: "hsl(var(--accent-anomaly))",
                  foreground: "hsl(var(--accent-anomaly-foreground))",
                },
                info: { // New semantic color
                  DEFAULT: "hsl(var(--info))",
                  foreground: "hsl(var(--info-foreground))",
                },
            },
            borderRadius: {
                lg: "var(--radius)",
                md: "calc(var(--radius) - 2px)",
                sm: "calc(var(--radius) - 4px)",
            },
            keyframes: {
                "accordion-down": { from: { height: "0" }, to: { height: "var(--radix-accordion-content-height)" } },
                "accordion-up": { from: { height: "var(--radix-accordion-content-height)" }, to: { height: "0" } },
                'pulse-subtle': { '0%, 100%': { opacity: '0.7' }, '50%': { opacity: '1' } }
            },
            animation: {
                "accordion-down": "accordion-down 0.2s ease-out",
                "accordion-up": "accordion-up 0.2s ease-out",
                'matrix-pulse': 'pulse-subtle 1.5s cubic-bezier(0.4, 0, 0.6, 1) infinite',
            },
        },
    },
    plugins: [
        tailwindcssAnimate,
    ],
}

export default config;