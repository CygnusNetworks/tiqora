/** The link's secret: the URL fragment (`/wallboard#<token>`), which no server log or referrer ever sees. */
export function readKioskToken(hash: string): string | null {
  const raw = hash.startsWith("#") ? hash.slice(1) : hash;
  let token = raw;
  try {
    token = decodeURIComponent(raw);
  } catch {
    // not percent-encoded: use as is
  }
  token = token.trim();
  return token === "" ? null : token;
}

/** The public wallboard page (`/wallboard`, not the logged-in TV mode `/agent/wallboard`): no login, no cookies. */
export function isPublicWallboardPath(pathname: string): boolean {
  return pathname === "/wallboard" || pathname.startsWith("/wallboard/");
}
