/**
 * Rich, representative mock dataset shared by the README screenshot fixture
 * (e2e/fixtures/rich-mock.ts) and the MSW-backed GitHub Pages demo
 * (src/demo/handlers.ts). English data; admin resource lists return the
 * `AdminPage` envelope, agent endpoints match the real API shapes.
 *
 * `resolveData(pathname, method)` is pure (no auth state) — each consumer layers
 * its own auth handling (the fixture gates behind a login flow; the demo is
 * auto-authenticated).
 */

function page<T>(items: T[]) {
  return { items, total: items.length, page: 1, page_size: 500 };
}

const t0 = "2026-06-01T00:00:00Z";

export const demoUser = {
  id: 1,
  login: "aturner",
  first_name: "Alex",
  last_name: "Turner",
  auth_method: "password",
  is_admin: true,
  email: "alex.turner@example.com",
};

// ── Queues (agent tree with counts) ─────────────────────────────────────────
const agentQueues = [
  { id: 1, name: "Support", group_id: 2, valid: true, counts: { open: 14, locked: 3, unlocked: 11, total: 22 },
    children: [
      { id: 2, name: "Support::Level 1", group_id: 2, parent_name: "Support", valid: true, counts: { open: 9, locked: 2, unlocked: 7, total: 13 }, children: [] },
      { id: 3, name: "Support::Level 2", group_id: 2, parent_name: "Support", valid: true, counts: { open: 5, locked: 1, unlocked: 4, total: 6 }, children: [] },
    ] },
  { id: 4, name: "Incidents", group_id: 3, valid: true, counts: { open: 7, locked: 1, unlocked: 6, total: 12 }, children: [] },
  { id: 5, name: "Sales", group_id: 4, valid: true, counts: { open: 4, locked: 0, unlocked: 4, total: 9 }, children: [] },
  { id: 6, name: "Billing", group_id: 5, valid: true, counts: { open: 3, locked: 0, unlocked: 3, total: 8 }, children: [] },
];

// ── Tickets ─────────────────────────────────────────────────────────────────
const CUSTOMERS = [
  { cid: "ACME", login: "j.doe@acme.example", name: "Jane Doe" },
  { cid: "ACME", login: "m.reed@acme.example", name: "Marcus Reed" },
  { cid: "NORTHWIND", login: "s.patel@northwind.example", name: "Sara Patel" },
  { cid: "NORTHWIND", login: "l.gomez@northwind.example", name: "Luis Gomez" },
  { cid: "GLOBEX", login: "k.wu@globex.example", name: "Karen Wu" },
  { cid: "INITECH", login: "t.hall@initech.example", name: "Tom Hall" },
];
const SUBJECTS = [
  "Printer offline in building A", "VPN access request", "Cannot log into portal",
  "Invoice discrepancy for March", "New laptop provisioning", "Email delivery delayed",
  "Password reset for shared mailbox", "Website contact form broken", "Slow database queries",
  "Request: additional license seats", "Two-factor app not accepting codes", "Onboarding new starter",
  "Firewall rule change request", "Backup job failed overnight",
];
const STATES = [
  { state_id: 1, state: "new", state_type: "open" },
  { state_id: 4, state: "open", state_type: "open" },
  { state_id: 6, state: "pending reminder", state_type: "pending reminder" },
  { state_id: 2, state: "closed successful", state_type: "closed" },
];
const PRIOS = [
  { priority_id: 2, priority: "2 low" },
  { priority_id: 3, priority: "3 normal" },
  { priority_id: 4, priority: "4 high" },
  { priority_id: 5, priority: "5 very high" },
];
const QROUTE = [
  { queue_id: 2, queue_name: "Support::Level 1" },
  { queue_id: 3, queue_name: "Support::Level 2" },
  { queue_id: 4, queue_name: "Incidents" },
  { queue_id: 5, queue_name: "Sales" },
  { queue_id: 6, queue_name: "Billing" },
];
const OWNERS = [
  { owner_id: 1, owner_login: "aturner", owner_name: "Alex Turner" },
  { owner_id: 2, owner_login: "bshah", owner_name: "Bianca Shah" },
  { owner_id: 3, owner_login: "cmorris", owner_name: "Chris Morris" },
];

const ticketItems = SUBJECTS.map((title, i) => {
  const c = CUSTOMERS[i % CUSTOMERS.length];
  const s = STATES[i % STATES.length];
  const p = PRIOS[(i * 3) % PRIOS.length];
  const q = QROUTE[i % QROUTE.length];
  const o = OWNERS[i % OWNERS.length];
  const day = 10 + (i % 12);
  return {
    id: 100 + i, tn: `2026070${String(1000 + i)}`, title,
    ...q, ...s, ...p, lock_id: 1, lock: "unlock", ...o,
    customer_id: c.cid, customer_user_id: c.login,
    create_time: `2026-07-${String(day).padStart(2, "0")}T09:12:00Z`,
    change_time: `2026-07-${String(Math.min(day + 2, 21)).padStart(2, "0")}T14:30:00Z`,
    age_seconds: (22 - day) * 86400, escalation_time: i % 5 === 0 ? 3600 : 0,
    escalation_response_time: 0, escalation_update_time: 0, escalation_solution_time: 0, until_time: 0,
  };
});
const tickets = { items: ticketItems, total: ticketItems.length, offset: 0, limit: 50 };

const ticketDetail = {
  ...ticketItems[0], type_id: 1, service_id: null, sla_id: null, responsible_user_id: 2,
  archive_flag: 0, create_by: 10, change_by: 1,
  dynamic_fields: [
    { name: "Category", label: "Category", field_type: "Dropdown", values: ["Hardware"] },
    { name: "Impact", label: "Impact", field_type: "Dropdown", values: ["High"] },
  ],
};
const articles = [
  { id: 500, ticket_id: 100, sender_type: "customer", sender_type_id: 3, communication_channel_id: 1,
    is_visible_for_customer: true, create_time: "2026-07-10T09:12:00Z", create_by: 10,
    subject: "Printer offline in building A", from_address: "j.doe@acme.example", to_address: "support@example.com",
    content_type: "text/html", incoming_time: 1720602720 },
  { id: 501, ticket_id: 100, sender_type: "agent", sender_type_id: 1, communication_channel_id: 1,
    is_visible_for_customer: true, create_time: "2026-07-10T10:02:00Z", create_by: 1,
    subject: "Re: Printer offline in building A", from_address: "support@example.com", to_address: "j.doe@acme.example",
    content_type: "text/html", incoming_time: 1720605720 },
  { id: 502, ticket_id: 100, sender_type: "agent", sender_type_id: 1, communication_channel_id: 3,
    is_visible_for_customer: false, create_time: "2026-07-10T10:05:00Z", create_by: 1,
    subject: "Internal note", from_address: "aturner", to_address: "", content_type: "text/plain", incoming_time: 1720605900 },
];
const bodies: Record<number, unknown> = {
  500: { article_id: 500, content_type: "text/html", is_html: true, body: "<p>Hi team, the main printer on floor 2 (building A) is offline since this morning. It shows a blinking amber light and won't respond. Several people can't print. Could you take a look?</p><p>Thanks,<br>Jane</p>" },
  501: { article_id: 501, content_type: "text/html", is_html: true, body: "<p>Hi Jane,</p><p>Thanks for reporting. We've reset the print spooler and pushed a firmware update. Could you try again and let us know?</p><p>Best,<br>Alex — IT Support</p>" },
  502: { article_id: 502, content_type: "text/plain", is_html: false, body: "Assigned to Level 2 — likely the fuser unit. Ordered a replacement, ETA tomorrow." },
};

const searchHits = {
  query: "server", estimated_total: 4,
  hits: ticketItems.slice(0, 4).map((t) => ({ id: t.id, tn: t.tn, title: t.title, queue_id: t.queue_id, queue_name: t.queue_name, state: t.state, state_type: t.state_type, priority: t.priority, owner_login: t.owner_login, customer_user_id: t.customer_user_id, create_time: t.create_time, excerpt: "…matched on the ticket subject and latest article…" })),
};

// KB categories match CategoryOut; articles match ArticleSummary[] (bare array).
const kbCategories = [
  { id: 1, name: "Getting started", slug: "getting-started", parent_id: null, sort: 10, valid: true, customer_visible: true, permission_group_ids: [2], create_time: t0, change_time: "2026-07-15T10:00:00Z" },
  { id: 2, name: "Email & calendar", slug: "email-calendar", parent_id: null, sort: 20, valid: true, customer_visible: true, permission_group_ids: [2], create_time: t0, change_time: "2026-07-14T09:00:00Z" },
  { id: 3, name: "VPN & remote access", slug: "vpn-remote", parent_id: null, sort: 30, valid: true, customer_visible: true, permission_group_ids: [2], create_time: t0, change_time: "2026-07-14T09:00:00Z" },
  { id: 4, name: "Printing", slug: "printing", parent_id: null, sort: 40, valid: true, customer_visible: false, permission_group_ids: [2], create_time: t0, change_time: "2026-07-18T16:00:00Z" },
];
const kbArticleItems = [
  { id: 700, title: "How to reset your password", slug: "how-to-reset-your-password", category_id: 1, state: "published", language: "en", version: 3, change_time: "2026-07-15T10:00:00Z" },
  { id: 701, title: "Setting up the VPN client", slug: "setting-up-the-vpn-client", category_id: 3, state: "published", language: "en", version: 2, change_time: "2026-07-14T09:00:00Z" },
  { id: 702, title: "Sharing a mailbox", slug: "sharing-a-mailbox", category_id: 2, state: "published", language: "en", version: 1, change_time: "2026-07-12T08:00:00Z" },
  { id: 703, title: "Fixing common printer problems", slug: "fixing-common-printer-problems", category_id: 4, state: "draft", language: "en", version: 1, change_time: "2026-07-18T16:00:00Z" },
  { id: 704, title: "Requesting a new laptop", slug: "requesting-a-new-laptop", category_id: 1, state: "published", language: "en", version: 1, change_time: "2026-07-11T11:00:00Z" },
  { id: 705, title: "Calendar sharing best practices", slug: "calendar-sharing-best-practices", category_id: 2, state: "review", language: "en", version: 2, change_time: "2026-07-16T14:00:00Z" },
];
const kbArticleDetail = {
  ...kbArticleItems[0],
  body_md: "# How to reset your password\n\n1. Open the portal and choose **Forgot password**.\n2. Enter your work email.\n3. Follow the link we send you (valid for 30 minutes).\n4. Choose a new password that meets the complexity rules.\n\nIf you still cannot sign in, open a ticket with the **Support** queue.",
  tags: ["password", "sso", "onboarding"],
  customer_visible: true,
  create_time: t0,
  create_by: 1,
  change_by: 1,
};

const historyEntries = [
  { id: 9001, ticket_id: 100, history_type_id: 1, history_type: "NewTicket", name: "%%Printer offline in building A%%", rendered: "Created ticket", create_by: 10, create_by_login: "j.doe@acme.example", create_time: "2026-07-10T09:12:00Z", owner_id: 1, article_id: 500 },
  { id: 9002, ticket_id: 100, history_type_id: 27, history_type: "OwnerUpdate", name: "%%aturner%%", rendered: "Owner set to aturner", create_by: 1, create_by_login: "aturner", create_time: "2026-07-10T09:40:00Z", owner_id: 1, article_id: null },
  { id: 9003, ticket_id: 100, history_type_id: 8, history_type: "StateUpdate", name: "%%new%%open%%", rendered: "State changed from new to open", create_by: 1, create_by_login: "aturner", create_time: "2026-07-10T09:41:00Z", owner_id: 1, article_id: null },
  { id: 9004, ticket_id: 100, history_type_id: 19, history_type: "AddNote", name: "%%Internal note%%", rendered: "Added note", create_by: 1, create_by_login: "aturner", create_time: "2026-07-10T10:05:00Z", owner_id: 1, article_id: 502 },
];

const onlineAgents = [
  { id: 1, login: "aturner", full_name: "Alex Turner", avatar_url: null },
  { id: 2, login: "bshah", full_name: "Bianca Shah", avatar_url: null },
  { id: 3, login: "cmorris", full_name: "Chris Morris", avatar_url: null },
];

// ── Stats (14-day series, richer) ───────────────────────────────────────────
const days = Array.from({ length: 14 }, (_, i) => `2026-07-${String(i + 8).padStart(2, "0")}`);
const volume = { granularity: "day", points: days.map((d, i) => ({ bucket: d, created: 6 + ((i * 3) % 7), closed: 4 + ((i * 2) % 6) })) };
const backlog = { granularity: "day", points: days.map((d, i) => ({ bucket: d, open_count: 40 + ((i * 5) % 18) })) };
const openSnapshot = { dimension: "queue", items: [
  { id: 2, label: "Support::Level 1", count: 13 }, { id: 3, label: "Support::Level 2", count: 6 },
  { id: 4, label: "Incidents", count: 12 }, { id: 5, label: "Sales", count: 9 }, { id: 6, label: "Billing", count: 8 },
] };
const sla = { total: 58, escalated: 6, first_response_breached: 3, update_breached: 1, solution_breached: 2,
  first_response_minutes: [12, 20, 35, 44, 51, 63, 22, 18], solution_minutes: [120, 240, 90, 310, 150] };
const agentWorkload = [
  { user_id: 1, login: "aturner", name: "Alex Turner", owned_open: 9, closed_in_period: 21 },
  { user_id: 2, login: "bshah", name: "Bianca Shah", owned_open: 7, closed_in_period: 18 },
  { user_id: 3, login: "cmorris", name: "Chris Morris", owned_open: 5, closed_in_period: 14 },
  { user_id: 4, login: "dpark", name: "Dana Park", owned_open: 4, closed_in_period: 11 },
];

const calendars = [{ id: 1, name: "Support on-call", color: "#2563eb", valid_id: 1 }];
const occurrences = [
  { id: 800, calendar_id: 1, title: "On-call: Alex", start_time: "2026-07-20T08:00:00Z", end_time: "2026-07-20T17:00:00Z", all_day: false, location: "Remote" },
  { id: 801, calendar_id: 1, title: "Maintenance window", start_time: "2026-07-22T22:00:00Z", end_time: "2026-07-23T02:00:00Z", all_day: false, location: "DC-1" },
];

// ── Admin data (envelope shape) ─────────────────────────────────────────────
const adminUsers = [
  ["aturner", "Alex", "Turner"], ["bshah", "Bianca", "Shah"], ["cmorris", "Chris", "Morris"],
  ["dpark", "Dana", "Park"], ["efox", "Erin", "Fox"], ["gliu", "Grace", "Liu"],
  ["hkaur", "Harpreet", "Kaur"], ["iowens", "Ivan", "Owens"],
].map(([login, f, l], i) => ({ id: i + 1, login, title: null, first_name: f, last_name: l, valid_id: i === 7 ? 2 : 1, create_time: t0, change_time: t0 }));
const adminGroups = [
  ["admin", "Administrators"], ["users", "All agents"], ["support", "Support team"],
  ["incidents", "Incident response"], ["sales", "Sales team"], ["billing", "Billing team"],
].map(([name, comments], i) => ({ id: i + 1, name, comments, valid_id: 1, create_time: t0, change_time: t0 }));
const adminRoles = [
  ["Agent", "Standard agent"], ["Supervisor", "Team lead / supervisor"],
  ["Read-only", "Read-only auditor"], ["Admin", "Full administrator"],
].map(([name, comments], i) => ({ id: i + 1, name, comments, valid_id: 1, create_time: t0, change_time: t0 }));
const adminQueuesFull = agentQueues.flatMap((q) => [q, ...q.children]).map((q) => ({
  id: q.id, name: q.name, group_id: q.group_id, unlock_timeout: 1440, first_response_time: 60,
  first_response_notify: 80, update_time: null, update_notify: null, solution_time: 480, solution_notify: 90,
  system_address_id: 1, calendar_name: null, default_sign_key: null, salutation_id: 1, signature_id: 1,
  follow_up_id: 1, follow_up_lock: 0, comments: null, valid_id: 1, create_time: t0, change_time: t0,
}));
const adminDynFields = [
  ["Category", "Dropdown", "Ticket"], ["Impact", "Dropdown", "Ticket"], ["Urgency", "Dropdown", "Ticket"],
  ["AssetTag", "Text", "Ticket"], ["ResolutionCode", "Dropdown", "Ticket"], ["CustomerSatisfaction", "Dropdown", "Ticket"],
].map(([name, ft, ot], i) => ({ id: i + 1, internal_field: 0, name, label: name, field_order: i + 1, field_type: ft, object_type: ot, config: {}, valid_id: 1, create_time: t0, change_time: t0 }));
const adminCustomerUsers = CUSTOMERS.map((c, i) => ({ id: i + 1, login: c.login, email: c.login, customer_id: c.cid, title: null, first_name: c.name.split(" ")[0], last_name: c.name.split(" ")[1], valid_id: 1, create_time: t0, change_time: t0 }));
const adminCustomerCompanies = [
  ["ACME", "ACME Corporation"], ["NORTHWIND", "Northwind Traders"], ["GLOBEX", "Globex Inc."], ["INITECH", "Initech LLC"],
].map(([customer_id, name], i) => ({ id: i + 1, customer_id, name, street: "1 Market St", zip: "90210", city: "Springfield", country: "US", url: null, comments: null, valid_id: 1, create_time: t0, change_time: t0 }));

// 2FA / auth config
const authConfigAgents = adminUsers.map((u, i) => ({
  user_id: u.id, login: u.login, name: `${u.first_name} ${u.last_name}`,
  totp_enabled: i % 2 === 0, webauthn_enabled: i % 3 === 0, passkey_count: i % 3 === 0 ? 1 : 0,
  two_factor_enabled: i % 2 === 0 || i % 3 === 0, sso_eligible: i % 4 === 0, enforce_2fa: i < 3,
  valid_id: u.valid_id,
}));

// GDPR preview
const gdprPreview = {
  mode: "anonymize",
  customers: CUSTOMERS.slice(0, 4).map((c, i) => ({ id: i + 1, login: c.login, email: c.login, customer_id: c.cid, first_name: c.name.split(" ")[0], last_name: c.name.split(" ")[1], valid_id: 1 })),
  counts: { customer_user: 4, customer_company: 2, ticket: 37, article: 214, article_data_mime: 214, customer_preferences: 12, customer_user_customer: 6, group_customer_user: 3 },
  sample: [], columns_changed: { customer_user: ["first_name", "last_name", "email", "phone"], ticket: ["customer_user_id"] }, tables_deleted: [],
};

// group/role assignment membership (for the assignment editors)
const groupCustomerUsers: Record<number, unknown[]> = { 3: [ { login: CUSTOMERS[0].login, permission: "rw" }, { login: CUSTOMERS[2].login, permission: "ro" } ] };
const customerUserGroups: Record<string, unknown[]> = { [CUSTOMERS[0].login]: [ { group_id: 3, name: "support", permission: "rw" } ] };
const groupUsers: Record<number, unknown[]> = { 3: [ { user_id: 1, login: "aturner", permission: "rw" }, { user_id: 2, login: "bshah", permission: "rw" } ] };

// Portal
export const demoPortalUser = { login: CUSTOMERS[0].login, customer_id: "ACME", first_name: "Jane", last_name: "Doe", email: CUSTOMERS[0].login };
export const demoPortalTickets = { items: ticketItems.slice(0, 4).map((t) => ({ id: t.id, tn: t.tn, title: t.title, state: t.state, state_type: t.state_type, queue_name: t.queue_name, create_time: t.create_time, change_time: t.change_time })), total: 4, offset: 0, limit: 50 };

export function resolveData(path: string, method: string): unknown | undefined {
  const p = path;
  // Auth
  if (p.endsWith("/auth/methods")) return { password: true, oidc: false, spnego: false, webauthn: true, ldap: false };
  if (p.endsWith("/auth/me")) return demoUser;
  if (p.endsWith("/auth/login") && method === "POST") return { user: demoUser };
  if (p.endsWith("/auth/logout")) return {};
  if (p.endsWith("/auth/totp")) return { enabled: true, confirmed: true };
  if (p.endsWith("/auth/passkey")) return [{ id: 1, name: "MacBook Touch ID", created: t0, last_used_at: "2026-07-19T08:00:00Z" }];
  // Agent
  if (p.endsWith("/api/v1/queues")) return agentQueues;
  if (p.endsWith("/api/v1/tickets/dashboard-summary")) return { my_open: 9, my_new: 3, unowned_new: 5, escalated: 6 };
  if (p.endsWith("/api/v1/tickets") && method === "GET") return tickets;
  if (p.match(/\/api\/v1\/tickets\/\d+\/articles\/\d+\/body$/)) { const id = Number(p.split("/").slice(-2)[0]); return bodies[id] ?? bodies[500]; }
  if (p.match(/\/api\/v1\/tickets\/\d+\/articles$/)) return articles;
  if (p.match(/\/api\/v1\/tickets\/\d+\/history$/)) return historyEntries;
  if (p.match(/\/api\/v1\/tickets\/\d+\/presence/)) return method === "GET" ? [] : {};
  if (p.match(/\/api\/v1\/tickets\/\d+\/attachments/) || p.match(/attachments/)) return [];
  if (p.match(/\/api\/v1\/tickets\/\d+$/) && method === "GET") return ticketDetail;
  if (p.endsWith("/api/v1/agents/online")) return onlineAgents;
  if (p.endsWith("/api/v1/agents/presence/ping") && method === "POST") return {};
  if (p.endsWith("/api/v1/search")) return searchHits;
  if (p.endsWith("/api/v1/kb/search")) return { query: "", results: kbArticleItems };
  if (p.endsWith("/api/v1/kb/categories")) return kbCategories;
  if (p.match(/\/api\/v1\/kb\/articles\/\d+$/) && method === "GET") return kbArticleDetail;
  if (p.endsWith("/api/v1/kb/articles")) return kbArticleItems;
  if (p.endsWith("/api/v1/kb/assignable-groups")) return [{ id: 2, name: "users" }, { id: 3, name: "support" }];
  if (p.endsWith("/api/v1/stats/volume")) return volume;
  if (p.endsWith("/api/v1/stats/backlog")) return backlog;
  if (p.endsWith("/api/v1/stats/open-snapshot")) return openSnapshot;
  if (p.endsWith("/api/v1/stats/sla")) return sla;
  if (p.endsWith("/api/v1/stats/agent-workload")) return agentWorkload;
  if (p.endsWith("/api/v1/calendar/calendars")) return calendars;
  if (p.endsWith("/api/v1/calendar/appointments")) return occurrences;
  if (p.includes("/api/v1/process")) return method === "GET" ? [] : {};
  // Admin — assignment editors (before the generic list handler)
  if (p.match(/\/admin\/groups\/\d+\/customer-users$/)) { const id = Number(p.split("/").slice(-2)[0]); return groupCustomerUsers[id] ?? []; }
  if (p.match(/\/admin\/groups\/\d+\/users$/)) { const id = Number(p.split("/").slice(-2)[0]); return groupUsers[id] ?? []; }
  if (p.match(/\/admin\/customer-users\/[^/]+\/groups$/)) { const login = decodeURIComponent(p.split("/").slice(-2)[0]); return customerUserGroups[login] ?? []; }
  // Admin — GDPR
  if (p.endsWith("/admin/gdpr/preview") && method === "POST") return gdprPreview;
  if (p.endsWith("/admin/gdpr/jobs")) return page([]);
  // Admin — auth config (2FA) uses the paginated envelope
  if (p.endsWith("/admin/auth-config")) return page(authConfigAgents);
  // Dynamic fields page consumes a bare array
  if (p.endsWith("/admin/dynamic-fields")) return adminDynFields;
  // Admin — AdminResourcePage lists expect the ENVELOPE
  if (p.endsWith("/admin/users")) return page(adminUsers);
  if (p.endsWith("/admin/groups")) return page(adminGroups);
  if (p.endsWith("/admin/roles")) return page(adminRoles);
  if (p.endsWith("/admin/queues")) return page(adminQueuesFull);
  if (p.endsWith("/admin/customer-users")) return page(adminCustomerUsers);
  if (p.endsWith("/admin/customer-companies")) return page(adminCustomerCompanies);
  if (p.endsWith("/admin/gdpr/jobs")) return page([]);
  // Everything else under /admin (aux lookups: system-addresses, salutations,
  // signatures, states, priorities, …) → bare array so `.map` consumers work.
  if (p.includes("/admin/") && method === "GET") return [];
  return undefined;
}

