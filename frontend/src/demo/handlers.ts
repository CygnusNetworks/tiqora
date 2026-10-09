import { http, HttpResponse } from "msw";
import { resolveData, demoPortalUser, demoPortalTickets, demoRefine } from "./mockData";

/**
 * MSW request handlers for the public demo build. Delegates to the shared pure
 * resolver (mockData.ts) and layers auto-authentication (the demo is always
 * "logged in" as the mock agent) + the portal plane. Any unmatched /api call
 * returns an empty payload so no screen errors on a missing endpoint.
 */
// Customer TOTP in the demo: enrolling and confirming flips the status for the session.
let demoPortalTotpEnabled = false;

function respond(pathname: string, method: string): unknown {
  // Portal plane (checked before the shared resolver's generic /auth/me).
  // One customer-audience provider (the admin demo's "microsoft", audience "both") so the
  // portal SSO button is visible in demo mode.
  if (pathname.endsWith("/api/portal/auth/methods"))
    return {
      password: true,
      providers: [{ slug: "microsoft", kind: "oidc", label: "Mit Microsoft anmelden", icon: "microsoft" }],
      webauthn: false,
    };
  if (pathname.endsWith("/api/portal/auth/totp/status")) return { enabled: demoPortalTotpEnabled };
  if (pathname.endsWith("/api/portal/auth/totp/enroll"))
    return { secret: "JBSWY3DPEHPK3PXP", otpauth_uri: "otpauth://totp/Tiqora:demo?secret=JBSWY3DPEHPK3PXP" };
  if (pathname.endsWith("/api/portal/auth/totp/confirm")) {
    demoPortalTotpEnabled = true;
    return { enabled: true };
  }
  if (pathname.endsWith("/api/portal/auth/totp") && method === "DELETE") {
    demoPortalTotpEnabled = false;
    return { enabled: false };
  }
  if (pathname.endsWith("/api/portal/auth/me")) return demoPortalUser;
  if (pathname.endsWith("/api/portal/tickets")) return demoPortalTickets;
  if (pathname.startsWith("/api/portal/")) return method === "GET" ? [] : {};

  const data = resolveData(pathname, method);
  if (data !== undefined) return data;
  return method === "GET" ? [] : {};
}

const DEMO_QR_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 21 21" width="200" height="200"><rect width="21" height="21" fill="#fff"/><path d="M0 0h7v7H0zM14 0h7v7h-7zM0 14h7v7H0z" fill="none" stroke="#000" stroke-width="2"/><path d="M2 2h3v3H2zM16 2h3v3h-3zM2 16h3v3H2zM9 1h2v2H9zM9 5h3v2H9zM8 9h2v2H8zM12 9h3v2h-3zM10 13h2v3h-2zM14 14h3v2h-3zM18 17h2v3h-2zM12 18h3v2h-3z" fill="#000"/></svg>';

export const handlers = [
  // Demo QR code for the customer TOTP enrolment (a placeholder, not a real secret).
  http.get(
    "*/api/portal/auth/totp/enroll/qr",
    () => new HttpResponse(DEMO_QR_SVG, { headers: { "Content-Type": "image/svg+xml" } }),
  ),
  // AI refine needs the request body (the segments to rewrite).
  http.post("*/api/v1/ai/refine", async ({ request }) =>
    HttpResponse.json(demoRefine((await request.json()) as Parameters<typeof demoRefine>[0])),
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
