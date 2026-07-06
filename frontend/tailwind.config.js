/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // UI/UX design system: dark ops dashboard + status green accent
        surface: {
          DEFAULT: '#0F172A',
          card: '#1E293B',
          muted: '#272F42',
          border: '#374151',
        },
        accent: { DEFAULT: '#22C55E', hover: '#16A34A' },
      },
      fontFamily: {
        sans: ['Fira Sans', 'system-ui', 'sans-serif'],
        mono: ['Fira Code', 'monospace'],
      },
    },
  },
  plugins: [],
}
