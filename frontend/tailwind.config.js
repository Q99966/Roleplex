// 保留现有组件的工具类接口，分别定义浅色背景、文字与边框，避免反转色阶造成低对比。
const ink = { 50: '#193747', 100: '#213e4d', 200: '#314e5d', 300: '#435e6d', 400: '#536f7d', 500: '#536f7d', 600: '#536f7d', 700: '#536f7d', 800: '#334f5e', 850: '#2b4857', 900: '#213e4d', 950: '#193747' }
const surface = { 50: '#ffffff', 100: '#f5fafc', 200: '#edf5f8', 300: '#e5eff3', 400: '#dce9ef', 500: '#d1e2e9', 600: '#d1e2e9', 700: '#e0edf2', 800: '#eaf3f7', 850: '#edf6f9', 900: '#f4fafc', 950: '#ffffff' }
const border = { 50: '#edf5f8', 100: '#e5eff4', 200: '#dce9f0', 300: '#d4e4eb', 400: '#bdd3de', 500: '#9fbcc9', 600: '#9fbcc9', 700: '#cadde6', 800: '#dce9ef', 850: '#e0edf2', 900: '#e5eff4', 950: '#e5eff4' }

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        slate: surface,
        indigo: { 50: '#f0f9ff', 100: '#e0f2fe', 200: '#bae6fd', 300: '#3992b5', 400: '#187caa', 500: '#087ba8', 600: '#096b94', 700: '#075878', 800: '#12566d', 900: '#d9eef6', 950: '#eaf6fb' },
        ink: '#213e4d', canvas: '#f3f9fc', panel: '#ffffff', line: '#dce9ef', brand: '#096b94',
      },
      textColor: {
        indigo: { 100: '#12566d', 200: '#12566d', 300: '#126a92', 400: '#126a92' },
        slate: ink, white: '#213e4d',
        emerald: { 100: '#176344', 200: '#176344', 300: '#18724f', 400: '#18724f', 500: '#17794e' },
        green: { 200: '#176344', 300: '#18724f', 400: '#18724f' },
        red: { 200: '#a62e46', 300: '#b72e49', 400: '#b72e49', 500: '#b72e49' },
        amber: { 200: '#8a5800', 300: '#8a5800', 400: '#946200', 500: '#946200' },
        blue: { 200: '#246f9a', 300: '#246f9a', 400: '#246f9a' },
        purple: { 300: '#75559d', 400: '#75559d' },
        cyan: { 200: '#16667f', 300: '#16667f', 400: '#16667f' },
      },
      backgroundColor: {
        slate: surface,
        emerald: { 500: '#1d805a', 600: '#18724f', 800: '#cdebdc', 900: '#e2f3e9', 950: '#edf9f1' },
        green: { 900: '#e2f3e9', 950: '#edf9f1' },
        red: { 500: '#c63650', 900: '#fbe1e7', 950: '#fff0f3' },
        amber: { 500: '#9c6800', 600: '#996500', 900: '#f8eacb', 950: '#fff6df' },
        blue: { 900: '#dfedf9', 950: '#edf6fd' },
        purple: { 900: '#eee5f7', 950: '#f6f0fc' },
      },
      borderColor: { slate: border },
      boxShadow: { panel: '0 12px 40px rgba(44, 95, 117, 0.08)', glow: '0 0 24px rgba(72, 166, 205, 0.12)' },
    },
  },
  plugins: [],
}
