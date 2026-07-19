import type { Page, Route } from "@playwright/test";

const user = {
  id: 1,
  login: "agent",
  first_name: "Ada",
  last_name: "Agent",
  auth_method: "password",
};

const queues = [
  {
    id: 1,
    name: "Raw",
    group_id: 1,
    valid: true,
    counts: { open: 2, locked: 0, unlocked: 2, total: 2 },
    children: [
      {
        id: 2,
        name: "Raw::Misc",
        group_id: 1,
        parent_name: "Raw",
        valid: true,
        counts: { open: 1, locked: 0, unlocked: 1, total: 1 },
        children: [],
      },
    ],
  },
];

const tickets = {
  items: [
    {
      id: 100,
      tn: "202607191000001",
      title: "Printer offline in building A",
      queue_id: 1,
      queue_name: "Raw",
      state_id: 1,
      state: "new",
      state_type: "open",
      priority_id: 3,
      priority: "3 normal",
      lock_id: 1,
      lock: "unlock",
      owner_id: 1,
      owner_login: "agent",
      owner_name: "Ada Agent",
      customer_id: "CUSTOMER",
      customer_user_id: "customer@example.com",
      create_time: "2026-07-18T10:00:00Z",
      change_time: "2026-07-19T08:00:00Z",
      age_seconds: 86400,
      escalation_time: 0,
      escalation_response_time: 0,
      escalation_update_time: 0,
      escalation_solution_time: 0,
      until_time: 0,
    },
    {
      id: 101,
      tn: "202607191000002",
      title: "VPN access request",
      queue_id: 2,
      queue_name: "Raw::Misc",
      state_id: 4,
      state: "open",
      state_type: "open",
      priority_id: 4,
      priority: "4 high",
      lock_id: 1,
      lock: "unlock",
      owner_id: 1,
      owner_login: "agent",
      owner_name: "Ada Agent",
      customer_id: "ACME",
      customer_user_id: "bob@acme.example",
      create_time: "2026-07-19T07:00:00Z",
      change_time: "2026-07-19T09:00:00Z",
      age_seconds: 3600,
      escalation_time: 0,
      escalation_response_time: 0,
      escalation_update_time: 0,
      escalation_solution_time: 0,
      until_time: 0,
    },
  ],
  total: 2,
  offset: 0,
  limit: 50,
};

const ticketDetail = {
  ...tickets.items[0],
  type_id: 1,
  service_id: null,
  sla_id: null,
  responsible_user_id: null,
  archive_flag: 0,
  create_by: 1,
  change_by: 1,
  dynamic_fields: [
    { name: "Process", label: "Process", field_type: "Text", values: ["IT"] },
  ],
};

const articles = [
  {
    id: 500,
    ticket_id: 100,
    sender_type: "customer",
    sender_type_id: 3,
    communication_channel_id: 1,
    is_visible_for_customer: true,
    create_time: "2026-07-18T10:00:00Z",
    create_by: 10,
    subject: "Printer offline in building A",
    from_address: "customer@example.com",
    to_address: "support@example.com",
    content_type: "text/html",
    incoming_time: 1721296800,
  },
];

const articleBody = {
  article_id: 500,
  content_type: "text/html",
  is_html: true,
  body:
    '<p>Hello, the printer is offline.</p>' +
    '<img src="/api/v1/tickets/100/articles/500/attachments/by-cid/inline1" alt="cid-fixture">' +
    '<img src="" data-external-src="https://tracker.example/pixel.gif" alt="ext">',
};

const attachments = [
  {
    id: 900,
    article_id: 500,
    filename: "screenshot.png",
    content_type: "image/png",
    content_size: "2048",
    content_id: "inline1",
    disposition: "inline",
  },
];

const history = [
  {
    id: 1,
    ticket_id: 100,
    name: "%%new%%open",
    history_type_id: 1,
    history_type: "StateUpdate",
    article_id: null,
    owner_id: 1,
    create_time: "2026-07-18T10:00:00Z",
    create_by: 1,
  },
];

const searchHits = {
  query: "printer",
  estimated_total: 1,
  hits: [
    {
      id: 100,
      tn: "202607191000001",
      title: "Printer offline in building A",
      queue_id: 1,
      queue_name: "Raw",
      state: "new",
      state_type: "open",
      priority: "3 normal",
      owner_login: "agent",
      customer_id: "CUSTOMER",
      create_time: "2026-07-18T10:00:00Z",
      change_time: "2026-07-19T08:00:00Z",
      excerpt: "Hello, the <em>printer</em> is offline.",
    },
  ],
};

let authenticated = false;

async function json(route: Route, status: number, body: unknown) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

/**
 * Intercept all /api/v1/* calls with deterministic fixtures.
 * Call once per test (or in beforeEach).
 */
export async function mockApi(page: Page) {
  authenticated = false;

  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();

    // Auth
    if (path.endsWith("/api/v1/auth/login") && method === "POST") {
      const body = req.postDataJSON() as { login?: string; password?: string };
      if (body.login === "agent" && body.password === "secret") {
        authenticated = true;
        await json(route, 200, { user });
        return;
      }
      await json(route, 401, { detail: "Invalid credentials" });
      return;
    }
    if (path.endsWith("/api/v1/auth/me") && method === "GET") {
      if (!authenticated) {
        await json(route, 401, { detail: "Not authenticated" });
        return;
      }
      await json(route, 200, user);
      return;
    }
    if (path.endsWith("/api/v1/auth/logout") && method === "POST") {
      authenticated = false;
      await route.fulfill({ status: 204, body: "" });
      return;
    }

    if (!authenticated) {
      await json(route, 401, { detail: "Not authenticated" });
      return;
    }

    if (path.endsWith("/api/v1/queues") && method === "GET") {
      await json(route, 200, queues);
      return;
    }

    if (path.match(/\/api\/v1\/tickets\/\d+\/articles\/\d+\/body$/)) {
      await json(route, 200, articleBody);
      return;
    }
    if (path.match(/\/api\/v1\/tickets\/\d+\/articles\/\d+\/attachments$/)) {
      await json(route, 200, attachments);
      return;
    }
    if (path.match(/\/api\/v1\/tickets\/\d+\/articles\/\d+\/attachments\/\d+/)) {
      await route.fulfill({
        status: 200,
        contentType: "image/png",
        body: Buffer.from([0x89, 0x50, 0x4e, 0x47]),
      });
      return;
    }
    if (path.match(/\/api\/v1\/tickets\/\d+\/articles$/)) {
      await json(route, 200, articles);
      return;
    }
    if (path.match(/\/api\/v1\/tickets\/\d+\/history$/)) {
      await json(route, 200, history);
      return;
    }
    if (path.match(/\/api\/v1\/tickets\/\d+$/)) {
      await json(route, 200, ticketDetail);
      return;
    }
    if (path.endsWith("/api/v1/tickets") && method === "GET") {
      await json(route, 200, tickets);
      return;
    }

    if (path.endsWith("/api/v1/search") && method === "GET") {
      const q = url.searchParams.get("q") || "";
      if (!q) {
        await json(route, 422, { detail: "q required" });
        return;
      }
      await json(route, 200, { ...searchHits, query: q });
      return;
    }

    await json(route, 404, { detail: `No mock for ${method} ${path}` });
  });
}

export async function loginAsAgent(page: Page) {
  await page.goto("/login");
  await page.getByTestId("login-username").fill("agent");
  await page.getByTestId("login-password").fill("secret");
  await page.getByTestId("login-submit").click();
  await page.waitForURL(/\/agent/);
}
