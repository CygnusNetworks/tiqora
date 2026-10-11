/** Theme through the CSSOM only: the frame CSP has no 'unsafe-inline' and colours are validated. */

export const DEFAULT_PRIMARY = "#2563eb";
const HEX = /^#[0-9a-f]{6}$/i;

export function applyTheme(
  root: HTMLElement,
  theme: { primaryColor: string; mode: "auto" | "light" | "dark" },
  dir: "ltr" | "rtl",
): void {
  root.style.setProperty("--tq-primary", HEX.test(theme.primaryColor) ? theme.primaryColor : DEFAULT_PRIMARY);
  root.setAttribute("data-theme", theme.mode === "light" || theme.mode === "dark" ? theme.mode : "auto");
  root.setAttribute("dir", dir === "rtl" ? "rtl" : "ltr");
}
