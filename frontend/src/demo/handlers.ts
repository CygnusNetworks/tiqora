import { http, HttpResponse } from "msw";
import { resolveData, demoPortalUser, demoPortalTickets } from "./mockData";

/**
 * MSW request handlers for the public demo build. Delegates to the shared pure
 * resolver (mockData.ts) and layers auto-authentication (the demo is always
 * "logged in" as the mock agent) + the portal plane. Any unmatched /api call
 * returns an empty payload so no screen errors on a missing endpoint.
 */
function respond(pathname: string, method: string): unknown {
  // Portal plane (checked before the shared resolver's generic /auth/me).
  if (pathname.endsWith("/api/portal/auth/methods")) return { password: true };
  if (pathname.endsWith("/api/portal/auth/me")) return demoPortalUser;
  if (pathname.endsWith("/api/portal/tickets")) return demoPortalTickets;
  if (pathname.startsWith("/api/portal/")) return method === "GET" ? [] : {};

  const data = resolveData(pathname, method);
  if (data !== undefined) return data;
  return method === "GET" ? [] : {};
}

export const handlers = [
  http.all("*/api/*", ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname.endsWith("/events/stream")) {
      return new HttpResponse("", { headers: { "content-type": "text/event-stream" } });
    }
    return HttpResponse.json(respond(url.pathname, request.method) as never);
  }),
];
