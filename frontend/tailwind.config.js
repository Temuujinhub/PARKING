/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        // Easy Parking Brandbook 2025: Green #4CAF52, Gray #585856/#7E7E7D
        // Өдөр/шөнийн горим: бүх өнгө CSS хувьсагчаар (index.css :root / .dark)
        surface: {
          DEFAULT: 'rgb(var(--surface) / <alpha-value>)',
          card: 'rgb(var(--surface-card) / <alpha-value>)',
          muted: 'rgb(var(--surface-muted) / <alpha-value>)',
          border: 'rgb(var(--surface-border) / <alpha-value>)',
        },
        // slate-ийг хувьсагчаар дарж бичсэнээр бүх текст хоёр горимд зөв харагдана
        slate: {
          100: 'rgb(var(--ink-100) / <alpha-value>)', 200: 'rgb(var(--ink-200) / <alpha-value>)',
          300: 'rgb(var(--ink-300) / <alpha-value>)', 400: 'rgb(var(--ink-400) / <alpha-value>)',
          500: 'rgb(var(--ink-500) / <alpha-value>)', 600: 'rgb(var(--ink-600) / <alpha-value>)',
          700: 'rgb(var(--ink-700) / <alpha-value>)', 900: 'rgb(var(--ink-900) / <alpha-value>)',
        },
        accent: { DEFAULT: '#4CAF52', hover: '#3F9A46' },
        brand: {
          green: '#4CAF52',
          green2: '#68BD45',
          gray: '#585856',
          gray2: '#7E7E7D',
          red: '#D64C45',
          teal: '#4DA9AE',
          blue: '#0D6EFF',
          orange: '#FF7300',
        },
      },
      fontFamily: {
        sans: ['PT Sans', 'system-ui', 'sans-serif'],
        mono: ['Fira Code', 'monospace'],
      },
    },
  },
  plugins: [],
}
