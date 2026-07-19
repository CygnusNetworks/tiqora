/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        surface: "var(--color-surface)",
        "surface-elevated": "var(--color-surface-elevated)",
        ink: "var(--color-ink)",
        muted: "var(--color-muted)",
        accent: "var(--color-accent)",
        border: "var(--color-border)",
        danger: "var(--color-danger)",
        warn: "var(--color-warn)",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
      },
    },
  },
  plugins: [],
};
