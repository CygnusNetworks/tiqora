import type { Page, Route } from "@playwright/test";

const customer = {
  id: 1,
  login: "customer@example.com",
  email: "customer@example.com",
  customer_id: "ACME",
  first_name: "Cara",
  last_name: "Customer",
};

const openTicket = {
  id: 200,
  tn: "202607191100001",
  title: "Cannot log in to the client portal",
  queue_id: 1,
  queue_name: "Support",
  state_id: 4,
  state: "open",
  state_type: "open",
  priority_id: 3,
  priority: "3 normal",
  lock_id: 1,
  lock: "unlock",
  owner_id: 1,
  owner_login: "agent",
  owner_name: "Ada Agent",
  customer_id: "ACME",
  customer_user_id: "customer@example.com",
  create_time: "2026-07-18T09:00:00Z",
  change_time: "2026-07-19T07:30:00Z",
  age_seconds: 90000,
  escalation_time: 0,
  escalation_response_time: 0,
  escalation_update_time: 0,
  escalation_solution_time: 0,
  until_time: 0,
};

const closedTicket = {
  ...openTicket,
  id: 201,
  tn: "202607181100002",
  title: "Invoice question",
  state_id: 2,
  state: "closed successful",
  state_type: "closed",
};

const newlyCreatedTicket = {
  ...openTicket,
  id: 300,
  tn: "202607191200003",
  title: "My printer is on fire",
  state_id: 1,
  state: "new",
  state_type: "open",
};

const tickets = {
  items: [openTicket, closedTicket],
  total: 2,
  offset: 0,
  limit: 100,
};

const ticketsById: Record<number, unknown> = {
  200: openTicket,
  201: closedTicket,
  300: newlyCreatedTicket,
};

const ticketArticles: Record<number, unknown[]> = {
  200: [
    {
      id: 900,
      ticket_id: 200,
      sender_type: "customer",
      sender_type_id: 3,
      communication_channel_id: 1,
      is_visible_for_customer: true,
      create_time: "2026-07-18T09:00:00Z",
      create_by: 1,
      subject: "Cannot log in to the client portal",
      from_address: "customer@example.com",
      to_address: "support@example.com",
      content_type: "text/plain",
    },
    {
      id: 901,
      ticket_id: 200,
      sender_type: "agent",
      sender_type_id: 1,
      communication_channel_id: 1,
      is_visible_for_customer: true,
      create_time: "2026-07-19T07:30:00Z",
      create_by: 2,
      subject: "Re: Cannot log in to the client portal",
      from_address: "support@example.com",
      to_address: "customer@example.com",
      content_type: "text/plain",
    },
  ],
  201: [],
};

const kbSearchHits = {
  query: "password",
  estimated_total: 1,
  hits: [
    {
      article_id: 50,
      chunk_id: 1,
      title: "Resetting your password",
      heading_path: "Account > Password",
      anchor: "reset",
      content: "To reset your password, click Forgot password on the login screen.",
      language: "en",
      state: "published",
      customer_visible: true,
      permission_group_id: null,
      score: 0.9,
    },
  ],
};

const kbArticle = {
  id: 50,
  title: "Resetting your password",
  slug: "resetting-your-password",
  language: "en",
  content_md: "# Resetting your password\n\nClick **Forgot password** on the login screen.",
  tags: ["account"],
};

let authenticated = false;
let nextArticleId = 950;

async function json(route: Route, status: number, body: unknown) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

/** Intercept all /api/portal/* calls with deterministic fixtures. */
export async function mockPortalApi(page: Page) {
  authenticated = false;

  await page.route("**/api/portal/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();

    if (path.endsWith("/api/portal/auth/login") && method === "POST") {
      const body = req.postDataJSON() as { login?: string; password?: string };
      if (body.login === "customer@example.com" && body.password === "secret") {
        authenticated = true;
        await json(route, 200, { customer });
        return;
      }
      await json(route, 401, { detail: "Invalid credentials" });
      return;
    }
    if (path.endsWith("/api/portal/auth/me") && method === "GET") {
      if (!authenticated) {
        await json(route, 401, { detail: "Not authenticated" });
        return;
      }
      await json(route, 200, customer);
      return;
    }
    if (path.endsWith("/api/portal/auth/logout") && method === "POST") {
      authenticated = false;
      await route.fulfill({ status: 204, body: "" });
      return;
    }

    if (!authenticated) {
      await json(route, 401, { detail: "Not authenticated" });
      return;
    }

    if (path.endsWith("/api/portal/kb/search") && method === "GET") {
      const q = url.searchParams.get("q") || "";
      if (!q) {
        await json(route, 422, { detail: "q required" });
        return;
      }
      if (!q.toLowerCase().includes("password")) {
        await json(route, 200, { query: q, estimated_total: 0, hits: [] });
        return;
      }
      await json(route, 200, { ...kbSearchHits, query: q });
      return;
    }
    if (path.match(/\/api\/portal\/kb\/articles\/.+$/)) {
      await json(route, 200, kbArticle);
      return;
    }

    if (path.match(/\/api\/portal\/tickets\/\d+\/attachments$/) && method === "POST") {
      const articleId = nextArticleId++;
      await json(route, 201, { article_id: articleId, attachment_ids: [1] });
      return;
    }
    if (path.match(/\/api\/portal\/tickets\/\d+\/reply$/) && method === "POST") {
      const articleId = nextArticleId++;
      await json(route, 200, { article_id: articleId, reopened: false });
      return;
    }
    if (path.match(/\/api\/portal\/tickets\/\d+\/articles$/) && method === "GET") {
      const id = Number(path.match(/\/tickets\/(\d+)\//)?.[1]);
      await json(route, 200, ticketArticles[id] ?? []);
      return;
    }
    if (path.match(/\/api\/portal\/tickets\/\d+$/) && method === "GET") {
      const id = Number(path.split("/").pop());
      await json(route, 200, ticketsById[id] ?? openTicket);
      return;
    }
    if (path.endsWith("/api/portal/tickets") && method === "GET") {
      await json(route, 200, tickets);
      return;
    }
    if (path.endsWith("/api/portal/tickets") && method === "POST") {
      await json(route, 201, { ticket_id: 300 });
      return;
    }

    await json(route, 404, { detail: `No mock for ${method} ${path}` });
  });
}

export async function loginAsCustomer(page: Page) {
  await page.goto("/portal/login");
  await page.getByTestId("portal-login-username").fill("customer@example.com");
  await page.getByTestId("portal-login-password").fill("secret");
  await page.getByTestId("portal-login-submit").click();
  await page.waitForURL(/\/portal(\/)?$/);
}
