/**
 * Theme colour, usable with an opacity modifier.
 *
 * Every colour here is a CSS variable so the themes can swap it, but Tailwind 3
 * can only apply an opacity modifier (`bg-accent/60`) to a colour written as a
 * function of `<alpha-value>`. Handed a bare `var(--color-accent)` it emits **no
 * rule at all** for the modified class — silently: the build succeeds and the
 * element simply ends up with no background/border colour, or inherits its text
 * colour. `color-mix` keeps the variable indirection while giving Tailwind the
 * alpha slot it needs. With no modifier Tailwind substitutes `1`, which mixes
 * back to the plain colour, so unmodified classes are unchanged.
 *
 * Tokens that are already semi-transparent (`--color-accent-dim` and the
 * article tints are `rgba(...)`) work here too — mixing them at 100% is a no-op.
 */
const themeColor = (name) =>
  `color-mix(in srgb, var(${name}) calc(<alpha-value> * 100%), transparent)`;

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,ts,jsx,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg: themeColor("--color-bg"),
        surface: themeColor("--color-surface"),
        "surface-elevated": themeColor("--color-surface-elevated"),
        "surface-subtle": themeColor("--color-surface-subtle"),
        ink: themeColor("--color-ink"),
        muted: themeColor("--color-muted"),
        accent: themeColor("--color-accent"),
        "accent-dim": themeColor("--color-accent-dim"),
        "accent-ink": themeColor("--color-accent-ink"),
        border: themeColor("--color-border"),
        hairline: themeColor("--color-hairline"),
        danger: themeColor("--color-danger"),
        warn: themeColor("--color-warn"),
        escalation: themeColor("--color-escalation"),
        green: themeColor("--color-green"),
        amber: themeColor("--color-amber"),
        red: themeColor("--color-red"),
        purple: themeColor("--color-purple"),
        state: {
          new: themeColor("--color-state-new"),
          open: themeColor("--color-state-open"),
          pending: themeColor("--color-state-pending"),
          closed: themeColor("--color-state-closed"),
          removed: themeColor("--color-state-removed"),
        },
        article: {
          customer: themeColor("--color-article-customer"),
          "customer-border": themeColor("--color-article-customer-border"),
          agent: themeColor("--color-article-agent"),
          system: themeColor("--color-article-system"),
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
        "otp-shake": {
          "0%, 100%": { transform: "translateX(0)" },
          "20%": { transform: "translateX(-6px)" },
          "40%": { transform: "translateX(6px)" },
          "60%": { transform: "translateX(-4px)" },
          "80%": { transform: "translateX(4px)" },
        },
      },
      animation: {
        "route-in": "route-in 150ms ease-out",
        "otp-shake": "otp-shake 0.32s ease-in-out",
      },
    },
  },
  plugins: [],
};
