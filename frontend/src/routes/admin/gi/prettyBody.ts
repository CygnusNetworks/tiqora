/** Pretty-print a JSON body; anything else (XML, text) is shown as it is. */
export function prettyBody(raw: string): string {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2);
  } catch {
    return raw;
  }
}
