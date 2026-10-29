import { http, HttpResponse } from "msw";
import {
  resolveData,
  resolveReportRun,
  demoPortalUser,
  demoPortalTickets,
  demoPortalProcess,
  demoRefine,
} from "./mockData";
import { demoDashboards, resolve as resolveDashboards } from "./dashboardsMock";
import { demoCmdb, demoCmdbJob, demoCmdbReport, resolveCmdb } from "./cmdbMock";
import { resolveKiosk } from "./kioskMock";
import { resolveItsmData } from "./itsmMock";
import { resolveChangeReply } from "./changeMock";
import { resolveApprovalLink, resolveApprovalsData } from "./approvalsMock";
import { resolve as resolveSchedules } from "./schedulesMock";
import { setDemoFactsBuilding } from "./reportsMock";
import { demoReportFile, resolveSavedRequest } from "./savedMock";
import type { ReportRunIn } from "@/lib/reportsApi";

/**
 * Demo switch for the "report data is still being built" state: open any page
 * with `?demo_facts=building` (and `?demo_facts=ready` to end it). Kept for the
 * tab in sessionStorage because the app drops unknown search params.
 */
function syncDemoFacts(): void {
  try {
    const flag = new URLSearchParams(window.location.search).get("demo_facts");
    if (flag === "building") sessionStorage.setItem("tiqora-demo-facts", "building");
    if (flag === "ready") sessionStorage.removeItem("tiqora-demo-facts");
    setDemoFactsBuilding(sessionStorage.getItem("tiqora-demo-facts") === "building");
  } catch {
    // no storage (private mode): keep the current state
  }
}

const CREATE_ROUTES = new Set(["*/api/v1/reports", "*/api/v1/saved-searches"]);

/** The demo ticket list `POST /tickets/query` pages through (the same one `GET /tickets` serves). */
function savedTicketPage(): { items: unknown[] } {
  return resolveData("/api/v1/tickets", "GET") as { items: unknown[] };
}

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
  if (pathname.startsWith("/api/portal/process")) {
    const data = demoPortalProcess(pathname, method);
    if (data !== undefined) return data;
  }
  if (pathname.startsWith("/api/portal/approvals")) {
    const data = resolveApprovalsData(pathname, method);
    if (data !== undefined) return data;
  }
  if (pathname.startsWith("/api/portal/")) return method === "GET" ? [] : {};

  if (pathname.includes("/api/v1/report")) syncDemoFacts();
  const data = resolveData(pathname, method);
  if (data !== undefined) return data;
  return method === "GET" ? [] : {};
}

const DEMO_QR_SVG =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 21 21" width="200" height="200"><rect width="21" height="21" fill="#fff"/><path d="M0 0h7v7H0zM14 0h7v7h-7zM0 14h7v7H0z" fill="none" stroke="#000" stroke-width="2"/><path d="M2 2h3v3H2zM16 2h3v3h-3zM2 16h3v3H2zM9 1h2v2H9zM9 5h3v2H9zM8 9h2v2H8zM12 9h3v2h-3zM10 13h2v3h-2zM14 14h3v2h-3zM18 17h2v3h-2zM12 18h3v2h-3z" fill="#000"/></svg>';

/** Dashboards need the request body / query, which resolveData never sees (R-DASH-DEMO-BODY). */
async function dashboardsResponse(request: Request): Promise<Response | undefined> {
  const url = new URL(request.url);
  const hasBody = request.method !== "GET" && request.method !== "DELETE";
  const body = hasBody ? await request.json().catch(() => ({})) : undefined;
  const out = resolveDashboards(request.method, url.pathname, body, url.searchParams);
  if (!out) return undefined;
  return out.status === 204 ? new HttpResponse(null, { status: 204 }) : HttpResponse.json(out.body as never, { status: out.status });
}

/** Report schedules and the Znuny stats import: bodies and queries matter, so they get their own handler. */
async function schedulesResponse(request: Request): Promise<Response | undefined> {
  const url = new URL(request.url);
  const hasBody = request.method !== "GET" && request.method !== "DELETE";
  const body = hasBody ? await request.json().catch(() => ({})) : undefined;
  const out = resolveSchedules(request.method, url.pathname, body, url.searchParams);
  if (!out) return undefined;
  if (url.pathname.includes("/report-runs/"))
    return new HttpResponse(out.body as string, {
      headers: { "content-type": "application/pdf", "content-disposition": 'attachment; filename="bericht.pdf"' },
    });
  return out.status === 204 ? new HttpResponse(null, { status: 204 }) : HttpResponse.json(out.body as never, { status: out.status });
}

/** CMDB (agent, admin and portal plane): bodies, queries and an in-memory store, so it has its own handler. */
async function cmdbResponse(request: Request): Promise<Response | undefined> {
  const url = new URL(request.url);
  const hasBody = request.method !== "GET" && request.method !== "DELETE";
  const multipart = request.headers.get("content-type")?.includes("multipart") === true;
  const body = hasBody ? (multipart ? await request.formData().catch(() => undefined) : await request.json().catch(() => ({}))) : undefined;
  const out = resolveCmdb(demoCmdb, request.method, url.pathname, body, url.searchParams);
  if (!out) return undefined;
  return out.status === 204 ? new HttpResponse(null, { status: 204 }) : HttpResponse.json(out.body as never, { status: out.status });
}

/** The wallboard answers from the same demo store; the secret comes in its own header. */
function kioskResponse(request: Request): Response | undefined {
  const out = resolveKiosk(demoDashboards, request.method, new URL(request.url).pathname, request.headers.get("X-Tiqora-Kiosk"));
  return out ? HttpResponse.json(out.body as never, { status: out.status }) : undefined;
}

export const handlers = [
  // Public approval link: the body decides (token `demo`, approve vs. reject).
  http.post("*/api/v1/approval-links/*", async ({ request }) => {
    const body = await request.json().catch(() => ({}));
    const out = resolveApprovalLink(new URL(request.url).pathname, "POST", body);
    return out ? HttpResponse.json(out.body as never, { status: out.status }) : HttpResponse.json({}, { status: 404 });
  }),
  // Approvals: the inbox and the portal list filter on `?state=`, which resolveData never sees.
  http.get("*/api/v1/approvals/inbox", ({ request }) => {
    const url = new URL(request.url);
    return HttpResponse.json(resolveApprovalsData(url.pathname, "GET", url.search) as never);
  }),
  http.get("*/api/portal/approvals", ({ request }) => {
    const url = new URL(request.url);
    return HttpResponse.json(resolveApprovalsData(url.pathname, "GET", url.search) as never);
  }),
  // ITSM: the CI list and service lookups filter on the query string, which resolveData never sees.
  http.get("*/api/v1/itsm/*", ({ request }) => {
    const url = new URL(request.url);
    // Explicit `!== undefined`: the ITSM mock answers some routes with `null` (e.g. recent-incident).
    const itsm = resolveItsmData(url.pathname, "GET", url.search);
    if (itsm !== undefined) return HttpResponse.json(itsm as never);
    const change = resolveChangeReply(url.pathname, "GET", url.search);
    return change === undefined ? HttpResponse.json({}, { status: 404 }) : HttpResponse.json((change.body ?? null) as never, { status: change.status });
  }),
  // The portal's maintenance schedule filters on the window, which resolveData never sees.
  http.get("*/api/portal/change-schedule", ({ request }) => {
    const url = new URL(request.url);
    const reply = resolveChangeReply(url.pathname, "GET", url.search);
    return reply === undefined ? HttpResponse.json({}, { status: 404 }) : HttpResponse.json((reply.body ?? null) as never, { status: reply.status });
  }),
  // Creating a change, a phase step and "Als Change führen" are stateful (the list, the change page
  // and the zoom show the result), so the body reaches the mock.
  ...["*/api/v1/itsm/changes", "*/api/v1/itsm/changes/:id/transition", "*/api/v1/tickets/:id/change"].map((route) =>
    http.post(route, async ({ request }) => {
      const reply = resolveChangeReply(new URL(request.url).pathname, "POST", "", undefined, await request.json().catch(() => ({})));
      return reply === undefined ? HttpResponse.json({}, { status: 404 }) : HttpResponse.json((reply.body ?? null) as never, { status: reply.status });
    }),
  ),
  // the portal switch on the change page keeps its state
  http.patch("*/api/v1/itsm/changes/:id", async ({ request }) => {
    const reply = resolveChangeReply(new URL(request.url).pathname, "PATCH", "", undefined, await request.json().catch(() => ({})));
    return reply === undefined ? undefined : HttpResponse.json((reply.body ?? null) as never, { status: reply.status });
  }),
  // jobs of the CMDB mock (bulk edit, export); any other job id falls through to the generic resolver
  http.get("*/api/v1/jobs/:id", ({ params }) => {
    const job = demoCmdbJob(demoCmdb, Number(params.id));
    return job ? HttpResponse.json(job as never) : undefined;
  }),
  http.get("*/api/v1/jobs/:id/files/:fid", ({ params }) => {
    const text = demoCmdbReport(demoCmdb, Number(params.id));
    return text === undefined ? undefined : new HttpResponse(text, { headers: { "content-type": "text/csv; charset=utf-8" } });
  }),
  http.all("*/api/portal/config-items*", async ({ request }) => (await cmdbResponse(request)) ?? HttpResponse.json({}, { status: 404 })),
  http.all("*/api/v1/admin/cmdb/*", async ({ request }) => (await cmdbResponse(request)) ?? HttpResponse.json([])),
  http.all("*/api/v1/cmdb/*", async ({ request }) => (await cmdbResponse(request)) ?? HttpResponse.json([])),
  http.all("*/api/v1/report-schedules*", async ({ request }) => (await schedulesResponse(request)) ?? HttpResponse.json([])),
  http.get("*/api/v1/report-runs/*", async ({ request }) => (await schedulesResponse(request)) ?? new HttpResponse(null, { status: 404 })),
  http.all("*/api/v1/reports/import/znuny*", async ({ request }) => (await schedulesResponse(request)) ?? HttpResponse.json({})),
  http.get("*/api/v1/kiosk/*", ({ request }) => kioskResponse(request) ?? HttpResponse.json({}, { status: 404 })),
  http.all("*/api/v1/admin/dashboard-kiosk-links*", async ({ request }) => (await dashboardsResponse(request)) ?? HttpResponse.json([])),
  http.all("*/api/v1/dashboards*", async ({ request }) => (await dashboardsResponse(request)) ?? HttpResponse.json([])),
  http.post("*/api/v1/dashboard-widget-types/*/data", async ({ request }) => (await dashboardsResponse(request)) ?? HttpResponse.json({})),
  http.all("*/api/v1/admin/dashboard-templates*", async ({ request }) => (await dashboardsResponse(request)) ?? HttpResponse.json({})),
  // Demo QR code for the customer TOTP enrolment (a placeholder, not a real secret).
  http.get(
    "*/api/portal/auth/totp/enroll/qr",
    () => new HttpResponse(DEMO_QR_SVG, { headers: { "Content-Type": "image/svg+xml" } }),
  ),
  // AI refine needs the request body (the segments to rewrite).
  http.post("*/api/v1/ai/refine", async ({ request }) =>
    HttpResponse.json(demoRefine((await request.json()) as Parameters<typeof demoRefine>[0])),
  ),
  // Report runs need the request body (report_key / definition), which resolveData never sees.
  http.post("*/api/v1/reports/run", async ({ request }) => {
    syncDemoFacts();
    const out = resolveReportRun((await request.json()) as ReportRunIn);
    // A long range runs in the background, like on a real server.
    return HttpResponse.json(out, { status: out.job ? 202 : 200 });
  }),
  // Report file download (JSON or YAML) of a built-in or saved report.
  http.get("*/api/v1/reports/:key/file", ({ request, params }) => {
    const format = new URL(request.url).searchParams.get("format") === "yaml" ? "yaml" : "json";
    const file = demoReportFile(decodeURIComponent(String(params.key)), format);
    if (!file) return new HttpResponse(null, { status: 404 });
    return new HttpResponse(file.body, {
      headers: {
        "content-type": file.contentType,
        "content-disposition": `attachment; filename="${file.filename}"`,
      },
    });
  }),
  // Saved reports/searches and the structured ticket query: the writes carry a body that
  // resolveData never sees, so these handlers feed it to the in-memory demo store.
  ...(["post", "put", "delete"] as const).flatMap((verb) =>
    [
      "*/api/v1/reports",
      "*/api/v1/reports/import-file",
      "*/api/v1/reports/:id",
      "*/api/v1/saved-searches",
      "*/api/v1/saved-searches/import/znuny",
      "*/api/v1/saved-searches/:id",
      "*/api/v1/saved-searches/:id/prefs",
      "*/api/v1/tickets/query",
      "*/api/v1/ticket-filters/count",
    ].map((route) =>
      http[verb](route, async ({ request }) => {
        const text = await request.text();
        const body = text ? (JSON.parse(text) as unknown) : undefined;
        const out = resolveSavedRequest(request.method, new URL(request.url).pathname, body, savedTicketPage());
        if (out === undefined) return undefined; // not a saved route/id: the catch-all answers
        if (out === null) return new HttpResponse(null, { status: 204 });
        return HttpResponse.json(out as never, { status: verb === "post" && CREATE_ROUTES.has(route) ? 201 : 200 });
      }),
    ),
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
