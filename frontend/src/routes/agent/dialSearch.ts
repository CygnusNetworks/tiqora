/** Search validation of `/agent/dial`. TanStack's default parseSearch
 * JSON-parses values, so `number=491717630944` arrives as a JS number. */
export function validateDialSearch(s: Record<string, unknown>): { number: string; ticket?: number } {
  const number =
    typeof s.number === "string" || typeof s.number === "number" ? String(s.number).trim() : "";
  const t = s.ticket;
  const id = typeof t === "string" && t.trim() === "" ? NaN : Number(t);
  const ticket = Number.isInteger(id) && id > 0 ? id : undefined;
  return { number, ticket };
}
