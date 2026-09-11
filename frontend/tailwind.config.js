/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // 方案一【Linear / 碳黑工程流】：将 slate 映射为纯正中性碳灰 (Zinc)，彻底消除冷蓝偏色
        slate: {
          50: '#fafafa',
          100: '#f4f4f5',
          200: '#e4e4e7',
          300: '#d4d4d8',
          400: '#a1a1aa',
          500: '#71717a',
          600: '#52525b',
          700: '#3f3f46',
          800: '#27272a',
          850: '#1f1f23',
          900: '#141417',
          950: '#09090b',
        },
        // 方案一【Linear / 碳黑工程流】：将 indigo 映射为极客琥珀金 (Amber)，消除紫蓝 AI 模板感
        indigo: {
          50: '#fffbeb',
          100: '#fef3c7',
          200: '#fde68a',
          300: '#fcd34d',
          400: '#fbbf24',
          // 实色按钮沿用白字，默认与悬停底色均保留足够对比度。
          500: '#b85c09',
          600: '#b45309',
          700: '#a14a08',
          800: '#92400e',
          900: '#78350f',
          950: '#451a03',
        },
        ink: '#18181b',
        canvas: '#09090b',
        panel: '#141417',
        line: '#27272a',
        brand: '#b45309',
      },
      boxShadow: {
        panel: '0 12px 36px rgba(0, 0, 0, 0.5)',
        glow: '0 0 20px rgba(245, 158, 11, 0.2)',
      },
    },
  },
  plugins: [],
}
