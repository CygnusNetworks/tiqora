import { cn } from "@/lib/cn";

/** "First Last", else the login. */
export const customerName = (c: {
  first_name?: string | null;
  last_name?: string | null;
  login: string;
}) => [c.first_name, c.last_name].filter(Boolean).join(" ").trim() || c.login;

/** Up to two initials for an avatar fallback. */
export const initialsOf = (name: string) =>
  name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? "")
    .join("") || "?";

/** Shared look of the customer action buttons (links and buttons alike). */
export const actionClass = (primary = false) =>
  cn(
    "inline-flex items-center gap-2 rounded-md border px-3 py-1.5 text-sm font-medium transition-colors duration-100",
    "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
    primary
      ? "border-transparent bg-accent text-accent-ink hover:opacity-90"
      : "border-hairline bg-surface text-ink hover:bg-surface-subtle",
  );

/** Small square icon-only variant for list rows. */
export const iconActionClass =
  "inline-flex h-7 w-7 items-center justify-center rounded-md text-muted hover:bg-surface-subtle hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-accent";

/** `"Brenner, Ilka" <i.brenner@x.de>` / `Ilka Brenner <…>` / a bare address. */
export function parseFrom(raw: string | null | undefined): {
  email: string;
  first_name: string;
  last_name: string;
} {
  const text = (raw ?? "").trim();
  const angle = text.match(/^(.*)<([^>]+)>\s*$/);
  const email = (angle ? angle[2] : text.includes("@") ? text : "").trim();
  const display = (angle ? angle[1] : "").trim().replace(/^"(.*)"$/, "$1").trim();
  if (!display || display.includes("@")) return { email, first_name: "", last_name: "" };
  if (display.includes(",")) {
    const [last, first] = display.split(",", 2).map((s) => s.trim());
    return { email, first_name: first ?? "", last_name: last ?? "" };
  }
  const parts = display.split(/\s+/);
  return {
    email,
    first_name: parts.slice(0, -1).join(" "),
    last_name: parts[parts.length - 1] ?? "",
  };
}
