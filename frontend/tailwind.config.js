/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        ink: '#202124',
        canvas: '#f6f7f9',
        panel: '#ffffff',
        line: '#e5e7eb',
        brand: '#6366f1',
      },
      boxShadow: { panel: '0 12px 36px rgba(31, 41, 55, 0.08)' },
    },
  },
  plugins: [],
}
