/** @type {import('tailwindcss').Config} */
module.exports = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
 
    // Or if using `src` directory:
    './src/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        'lcd-bg': 'var(--lcd-bg)',
        'lcd-text': 'var(--lcd-text)',
      },
      fontFamily: {
        lcd: ['var(--font-lcd)'],
      },
    },
  },
  plugins: [
    require("tailwindcss-animate")
  ],
}

