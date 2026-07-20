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

const categories = [
  {
    id: 1,
    parent_id: null,
    name: "General",
    slug: "general",
    permission_group_id: null,
    customer_visible: true,
    sort: 0,
    valid: true,
    create_time: "2026-07-01T00:00:00Z",
    change_time: "2026-07-01T00:00:00Z",
  },
  {
    id: 2,
    parent_id: 1,
    name: "Printers",
    slug: "printers",
    permission_group_id: null,
    customer_visible: true,
    sort: 0,
    valid: true,
    create_time: "2026-07-01T00:00:00Z",
    change_time: "2026-07-01T00:00:00Z",
  },
];

const kbArticleSummaries = [
  {
    id: 700,
    category_id: 2,
    title: "Printer offline troubleshooting",
    slug: "printer-offline-troubleshooting",
    language: "en",
    state: "draft",
    version: 2,
    change_time: "2026-07-18T09:00:00Z",
  },
];

function initialKbArticle() {
  return {
    id: 700,
    category_id: 2,
    title: "Printer offline troubleshooting",
    slug: "printer-offline-troubleshooting",
    language: "en",
    state: "draft",
    content_md: "# Printer offline\n\nCheck the power cable and network link.",
    version: 2,
    create_by: 1,
    create_time: "2026-07-17T09:00:00Z",
    change_by: 1,
    change_time: "2026-07-18T09:00:00Z",
    tags: ["hardware", "printer"],
  };
}

let kbArticleFull = initialKbArticle();

const kbArticleVersions = [
  {
    id: 1,
    article_id: 700,
    version: 1,
    title: "Printer offline troubleshooting",
    content_md: "# Printer offline\n\nRestart the printer.",
    changed_by: 1,
    changed_at: "2026-07-17T09:00:00Z",
  },
  {
    id: 2,
    article_id: 700,
    version: 2,
    title: "Printer offline troubleshooting",
    content_md: "# Printer offline\n\nCheck the power cable and network link.",
    changed_by: 1,
    changed_at: "2026-07-18T09:00:00Z",
  },
];

const kbSearchHits = {
  query: "printer",
  estimated_total: 1,
  hits: [
    {
      article_id: 700,
      chunk_id: 1,
      title: "Printer offline troubleshooting",
      heading_path: "Printer offline",
      anchor: "printer-offline",
      content: "Check the power cable and network link.",
      language: "en",
      state: "published",
      customer_visible: true,
      permission_group_id: null,
      score: 1,
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
  kbArticleFull = initialKbArticle();

  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();

    // Auth
    if (path.endsWith("/api/v1/auth/methods") && method === "GET") {
      await json(route, 200, { password: true, oidc: false, spnego: false });
      return;
    }
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

    // Realtime SSE stream — mocked as a no-op: fulfil with a single event
    // (or nothing) and let the connection end. useSSE.ts is written
    // defensively (EventSource's own auto-reconnect handles a closed/failed
    // connection without throwing), so this keeps e2e green without needing
    // a real long-lived stream.
    if (path.endsWith("/api/v1/events/stream") && method === "GET") {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: ": mock-stream\n\n",
      });
      return;
    }

    if (path.match(/\/api\/v1\/tickets\/\d+\/presence$/) && method === "POST") {
      await route.fulfill({ status: 204, body: "" });
      return;
    }
    if (path.match(/\/api\/v1\/tickets\/\d+\/presence$/) && method === "GET") {
      await json(route, 200, []);
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
    if (path.match(/\/api\/v1\/tickets\/\d+\/articles$/) && method === "POST") {
      await json(route, 201, { article_id: 999 });
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

    // Knowledge base (agent)
    if (path.endsWith("/api/v1/kb/search") && method === "GET") {
      const q = url.searchParams.get("q") || "";
      if (!q) {
        await json(route, 422, { detail: "q required" });
        return;
      }
      await json(route, 200, { ...kbSearchHits, query: q });
      return;
    }
    if (path.endsWith("/api/v1/kb/categories") && method === "GET") {
      await json(route, 200, categories);
      return;
    }
    if (path.match(/\/api\/v1\/kb\/articles\/\d+\/versions$/) && method === "GET") {
      await json(route, 200, kbArticleVersions);
      return;
    }
    if (path.match(/\/api\/v1\/kb\/articles\/\d+\/publish$/) && method === "POST") {
      kbArticleFull = { ...kbArticleFull, state: "published", version: kbArticleFull.version + 1 };
      await json(route, 200, kbArticleFull);
      return;
    }
    if (path.match(/\/api\/v1\/kb\/articles\/\d+$/) && method === "GET") {
      await json(route, 200, kbArticleFull);
      return;
    }
    if (path.match(/\/api\/v1\/kb\/articles\/\d+$/) && method === "PATCH") {
      const body = req.postDataJSON() as Record<string, unknown>;
      kbArticleFull = { ...kbArticleFull, ...body, version: kbArticleFull.version + 1 } as typeof kbArticleFull;
      await json(route, 200, kbArticleFull);
      return;
    }
    if (path.endsWith("/api/v1/kb/articles") && method === "POST") {
      const body = req.postDataJSON() as Record<string, unknown>;
      const created = {
        ...kbArticleFull,
        id: 701,
        state: "draft",
        version: 1,
        tags: [],
        ...body,
      };
      await json(route, 201, created);
      return;
    }
    if (path.endsWith("/api/v1/kb/articles") && method === "GET") {
      await json(route, 200, kbArticleSummaries);
      return;
    }

    // Stats / reports
    if (path.endsWith("/api/v1/stats/volume") && method === "GET") {
      await json(route, 200, {
        granularity: "day",
        points: [
          { bucket: "2026-07-18", created: 3, closed: 1 },
          { bucket: "2026-07-19", created: 2, closed: 2 },
        ],
      });
      return;
    }
    if (path.endsWith("/api/v1/stats/backlog") && method === "GET") {
      await json(route, 200, {
        granularity: "day",
        points: [
          { bucket: "2026-07-18", open_count: 2 },
          { bucket: "2026-07-19", open_count: 2 },
        ],
      });
      return;
    }
    if (path.endsWith("/api/v1/stats/open-snapshot") && method === "GET") {
      await json(route, 200, {
        dimension: url.searchParams.get("dimension") || "queue",
        items: [
          { id: 1, label: "Raw", count: 2 },
          { id: 2, label: "Raw::Misc", count: 1 },
        ],
      });
      return;
    }
    if (path.endsWith("/api/v1/stats/sla") && method === "GET") {
      await json(route, 200, {
        total: 5,
        escalated: 1,
        first_response_breached: 1,
        update_breached: 0,
        solution_breached: 0,
        first_response_minutes: [30, 45],
        solution_minutes: [120],
      });
      return;
    }
    if (path.endsWith("/api/v1/stats/agent-workload") && method === "GET") {
      await json(route, 200, [
        { user_id: 1, login: "agent", name: "Ada Agent", owned_open: 2, closed_in_period: 1 },
      ]);
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
