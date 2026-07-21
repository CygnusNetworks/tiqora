import type { Page, Route } from "@playwright/test";
import {
  resolveData,
  demoUser as user,
  demoPortalUser as portalUser,
  demoPortalTickets as portalTickets,
} from "../../src/demo/mockData";

/**
 * Playwright adapter over the shared rich mock dataset (src/demo/mockData.ts).
 * Used only by the README screenshot generator (e2e/screenshots.spec.ts) — the
 * normal e2e suite keeps its own fixtures. Layers stateful auth over the pure
 * data resolver so the login form → /agent redirect works.
 */

async function json(route: Route, status: number, body: unknown) {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}

export async function mockRich(page: Page) {
  let authed = false;
  await page.route("**/api/**", async (r) => {
    const url = new URL(r.request().url());
    const method = r.request().method();
    const p = url.pathname;
    if (p.endsWith("/events/stream")) {
      await r.fulfill({ status: 200, contentType: "text/event-stream", body: "" });
      return;
    }
    // Agent auth plane (flag so the login form → /agent redirect works).
    if (p.endsWith("/api/v1/auth/methods")) {
      await json(r, 200, { password: true, oidc: false, spnego: false, webauthn: true, ldap: false });
      return;
    }
    if (p.endsWith("/api/v1/auth/login") && method === "POST") { authed = true; await json(r, 200, { user }); return; }
    if (p.endsWith("/api/v1/auth/me")) {
      if (!authed) { await json(r, 401, { detail: "Not authenticated" }); return; }
      await json(r, 200, user);
      return;
    }
    if (p.endsWith("/api/v1/auth/logout")) { authed = false; await r.fulfill({ status: 204, body: "" }); return; }
    // Portal auth plane — always authenticated for the demo/screenshots.
    if (p.endsWith("/api/portal/auth/methods")) { await json(r, 200, { password: true }); return; }
    if (p.endsWith("/api/portal/auth/me")) { await json(r, 200, portalUser); return; }
    if (p.endsWith("/api/portal/tickets")) { await json(r, 200, portalTickets); return; }
    if (p.startsWith("/api/portal/")) { await json(r, method === "GET" ? 200 : 204, method === "GET" ? [] : {}); return; }
    if (p.startsWith("/api/v1/") && !authed) { await json(r, 401, { detail: "Not authenticated" }); return; }
    const body = resolveData(p, method);
    if (body === undefined) { await json(r, method === "GET" ? 200 : 204, method === "GET" ? [] : {}); return; }
    await json(r, 200, body);
  });
}

export async function loginAsAgentRich(page: Page) {
  await mockRich(page);
  await page.goto("/login");
  await page.getByTestId("login-username").fill("aturner");
  await page.getByTestId("login-password").fill("secret");
  await page.getByTestId("login-submit").click();
  await page.waitForURL(/\/agent/);
}
