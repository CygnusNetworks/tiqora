/** Formatting and parsing for the money inputs in the admin forms.
 *
 * These fields used `<input type="number">`, which cannot show "1.00" (the
 * browser renders the bare number) and cannot carry a currency symbol. So they
 * are text inputs that format while idle and hand back a plain string while
 * being edited — the standard money-input shape.
 */

/** Symbol for the three currencies the provider form offers, falling back to
 * the code itself so an unknown one still renders something truthful. */
export function currencySymbol(code: string | null | undefined): string {
  switch ((code || "").toUpperCase()) {
    case "EUR":
      return "€";
    case "USD":
      return "$";
    case "GBP":
      return "£";
    default:
      return (code || "").toUpperCase();
  }
}

/** Digits a price is written with. Per-1M-token prices run to fractions of a
 * cent, so they get more than the two a budget needs. */
export type MoneyPrecision = "budget" | "price";

const DIGITS: Record<MoneyPrecision, { min: number; max: number }> = {
  budget: { min: 2, max: 2 },
  price: { min: 2, max: 4 },
};

/** Display form: locale-grouped, fixed decimals. Empty stays empty — a blank
 * field means "not configured", which must not render as "0.00". */
export function formatMoney(
  value: number | string | null | undefined,
  locale: string,
  precision: MoneyPrecision = "budget",
): string {
  if (value === null || value === undefined || value === "") return "";
  const n = typeof value === "number" ? value : Number(String(value).replace(",", "."));
  if (!Number.isFinite(n)) return String(value);
  const { min, max } = DIGITS[precision];
  return new Intl.NumberFormat(locale, {
    minimumFractionDigits: min,
    maximumFractionDigits: max,
  }).format(n);
}

/** Editing form: the bare number, no grouping — a thousands separator left in
 * the box would be re-parsed as a decimal point in some locales. */
export function toEditableMoney(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  return String(value);
}

/** Parse what someone typed. Accepts both separators because a German keyboard
 * produces "1,50" and a pasted value is usually "1.50"; returns `null` for an
 * empty field, which is what clears the column. */
export function parseMoney(raw: string): number | null | "invalid" {
  const trimmed = raw.trim();
  if (trimmed === "") return null;
  // Group separators are dropped before the last separator is read as the
  // decimal point, so "1.234,56" and "1,234.56" both land on 1234.56.
  const lastComma = trimmed.lastIndexOf(",");
  const lastDot = trimmed.lastIndexOf(".");
  const decimalAt = Math.max(lastComma, lastDot);
  let normalized = trimmed;
  if (decimalAt >= 0) {
    const head = trimmed.slice(0, decimalAt).replace(/[.,\s]/g, "");
    const tail = trimmed.slice(decimalAt + 1);
    normalized = `${head}.${tail}`;
  }
  const n = Number(normalized);
  if (!Number.isFinite(n) || n < 0) return "invalid";
  return n;
}
