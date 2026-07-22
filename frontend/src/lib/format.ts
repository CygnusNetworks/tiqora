/** Relative age / datetime helpers for agent tables. */

export function formatAgeSeconds(
  ageSeconds: number | null | undefined,
  locale: string,
): string {
  if (ageSeconds == null || Number.isNaN(ageSeconds)) return "—";
  const abs = Math.max(0, Math.floor(ageSeconds));
  const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
  if (abs < 60) return rtf.format(-abs, "second");
  if (abs < 3600) return rtf.format(-Math.floor(abs / 60), "minute");
  if (abs < 86400) return rtf.format(-Math.floor(abs / 3600), "hour");
  if (abs < 86400 * 30) return rtf.format(-Math.floor(abs / 86400), "day");
  return rtf.format(-Math.floor(abs / (86400 * 30)), "month");
}

export function formatDateTime(
  value: string | Date | null | undefined,
  locale: string,
): string {
  if (!value) return "—";
  const d = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat(locale, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(d);
}

/** Date-only (no time-of-day) formatting, e.g. for expiry previews. */
export function formatDateOnly(
  value: string | Date | null | undefined,
  locale: string,
): string {
  if (!value) return "—";
  const d = typeof value === "string" ? new Date(value) : value;
  if (Number.isNaN(d.getTime())) return "—";
  return new Intl.DateTimeFormat(locale, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(d);
}

export function formatBytes(size: string | number | null | undefined): string {
  if (size == null || size === "") return "—";
  const n = typeof size === "string" ? Number(size) : size;
  if (!Number.isFinite(n)) return String(size);
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

export function isEscalated(epoch: number | undefined | null): boolean {
  if (!epoch || epoch <= 0) return false;
  return epoch * 1000 < Date.now();
}
