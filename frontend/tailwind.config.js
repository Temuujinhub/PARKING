/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // Easy Parking Brandbook 2025: Green #4CAF52, Gray #585856/#7E7E7D
        surface: {
          DEFAULT: '#111312',
          card: '#1C1F1D',
          muted: '#282C29',
          border: '#3A3F3B',
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
