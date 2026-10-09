import { http, HttpResponse } from "msw";
import {
  resolveData,
  resolveReportRun,
  demoPortalUser,
  demoPortalTickets,
  demoRefine,
} from "./mockData";
import type { ReportRunIn } from "@/lib/reportsApi";

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
  // AI refine needs the request body (the segments to rewrite).
  http.post("*/api/v1/ai/refine", async ({ request }) =>
    HttpResponse.json(demoRefine((await request.json()) as Parameters<typeof demoRefine>[0])),
  ),
  // Report runs need the request body (report_key / definition), which resolveData never sees.
  http.post("*/api/v1/reports/run", async ({ request }) =>
    HttpResponse.json(resolveReportRun((await request.json()) as ReportRunIn)),
  ),
  // Report export download: a tiny XLSX-typed body (declared before the CSV catch-all below).
  http.get(
    "*/api/v1/jobs/9201/files/*",
    () =>
      new HttpResponse("PK demo", {
        headers: {
          "content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
          "content-disposition": 'attachment; filename="bericht.xlsx"',
        },
      }),
  ),
  // No pictures in the demo: the avatar proxy "has nothing", so every
  // avatar shows its initials (the catch-all's JSON would be a broken image).
  http.get("*/api/v1/avatars/*", () => new HttpResponse(null, { status: 404 })),
  // Job result download (the shared resolver only speaks JSON).
  http.get(
    "*/api/v1/jobs/*/files/*",
    () =>
      new HttpResponse("Ticket;Titel;Status\n2026100801;Beispiel;offen\n", {
        headers: {
          "content-type": "text/csv",
          "content-disposition": 'attachment; filename="tickets.csv"',
        },
      }),
  ),
  http.all("*/api/*", ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname.endsWith("/events/stream")) {
      return new HttpResponse("", { headers: { "content-type": "text/event-stream" } });
    }
    return HttpResponse.json(respond(url.pathname, request.method) as never);
  }),
];
