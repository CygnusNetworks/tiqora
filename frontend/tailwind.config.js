/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg: "var(--color-bg)",
        surface: "var(--color-surface)",
        "surface-elevated": "var(--color-surface-elevated)",
        "surface-subtle": "var(--color-surface-subtle)",
        ink: "var(--color-ink)",
        muted: "var(--color-muted)",
        accent: "var(--color-accent)",
        "accent-dim": "var(--color-accent-dim)",
        "accent-ink": "var(--color-accent-ink)",
        border: "var(--color-border)",
        hairline: "var(--color-hairline)",
        danger: "var(--color-danger)",
        warn: "var(--color-warn)",
        escalation: "var(--color-escalation)",
        green: "var(--color-green)",
        amber: "var(--color-amber)",
        red: "var(--color-red)",
        purple: "var(--color-purple)",
        state: {
          new: "var(--color-state-new)",
          open: "var(--color-state-open)",
          pending: "var(--color-state-pending)",
          closed: "var(--color-state-closed)",
          removed: "var(--color-state-removed)",
        },
        article: {
          customer: "var(--color-article-customer)",
          "customer-border": "var(--color-article-customer-border)",
          agent: "var(--color-article-agent)",
          system: "var(--color-article-system)",
        },
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
        display: ["var(--font-display)", "var(--font-sans)", "sans-serif"],
      },
      fontSize: {
        xs: ["12px", { lineHeight: "16px" }],
        sm: ["13px", { lineHeight: "18px" }],
        base: ["14px", { lineHeight: "20px" }],
        lg: ["16px", { lineHeight: "22px" }],
        xl: ["20px", { lineHeight: "26px" }],
        "2xl": ["28px", { lineHeight: "34px" }],
      },
      keyframes: {
        "route-in": {
          "0%": { opacity: "0", transform: "translateY(8px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "route-in": "route-in 150ms ease-out",
      },
    },
  },
  plugins: [],
};
