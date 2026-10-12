import { test, type Page, type Route, type TestInfo } from "@playwright/test";
import { GONE_SAVED_SEARCH_ID, createDemoDashboards, demoDefaultWidgets, demoTemplateUpdateHint, resolveDashboards, type DemoDashboards } from "../../src/demo/dashboardsMock";
import { createDemoCmdb, resolveCmdb, type DemoCmdb } from "../../src/demo/cmdbMock";
import { resolveKiosk } from "../../src/demo/kioskMock";
import type { ReportRunIn } from "../../src/lib/reportsApi";
// Reports reuse the demo's pure report data (catalogue definitions + deterministic results).
import { DEMO_FACTS, DEMO_REPORT_CATALOGUE, demoReport, demoSources, resolveReportRun } from "../../src/demo/reportsMock";
import {
  DEMO_SHARE_GROUPS,
  demoSavedReports,
  demoSavedSearches,
  demoTicketFilterFields,
  demoZnunyPreview,
} from "../../src/demo/savedMock";
import { createDemoSchedules, demoZnunyApply, resolveSchedules, type DemoSchedules } from "../../src/demo/schedulesMock";
import { filterDebugEntries, resolveGiData } from "../../src/demo/giMock";
import type { ProcessDialogFormData, TicketProcessState } from "../../src/lib/processApi";
import type { ReportItem, SavedReportIn, SavedReportUpdateIn } from "../../src/lib/reportsApi";
import type {
  SavedSearch,
  SavedSearchIn,
  SavedSearchPrefsIn,
  SavedSearchUpdateIn,
  ZnunyImportPreview,
  ZnunyImportResult,
} from "../../src/lib/savedSearchesApi";

const user = {
  id: 1,
  login: "agent",
  first_name: "Ada",
  last_name: "Agent",
  auth_method: "password",
  // Default agent is treated as admin so /admin e2e fixtures work without an
  // extra capability probe. Override via route when testing the access denied
  // path (see e2e/admin-access.spec.ts).
  is_admin: true,
  // The reports nav entry and routes need this (the server decides: reporting.access).
  can_view_reports: true,
  can_create_reports: true,
  can_share_reports: true,
  report_external_recipients: true,
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
  // The agent may write — enables the reply/note composers in the zoom view.
  can_write: true,
  permissions: { ro: true, note: true, rw: true },
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

const calendars = [
  { id: 1, group_id: 1, name: "Team Calendar", color: "#3b82f6", valid: true },
  { id: 2, group_id: 1, name: "On-call", color: "#ef4444", valid: true },
];

// Mid-month in the current month so the appointment is always visible in the
// calendar's default (current-month) grid regardless of when the tests run.
const _now = new Date();
const _ym = `${_now.getFullYear()}-${String(_now.getMonth() + 1).padStart(2, "0")}`;
const appointmentStart = `${_ym}-15T09:00:00`;
const appointmentEnd = `${_ym}-15T10:00:00`;

const calendarAppointment = {
  id: 900,
  parent_id: null,
  calendar_id: 1,
  unique_id: "900@tiqora",
  title: "Sprint planning",
  description: "Plan next sprint",
  location: "Room 2",
  start_time: appointmentStart,
  end_time: appointmentEnd,
  all_day: false,
  team_id: null,
  resource_id: null,
  recurring: false,
  recur_type: null,
  recur_interval: null,
  recur_count: null,
  recur_until: null,
  create_time: "2026-07-18T09:00:00Z",
  change_time: "2026-07-18T09:00:00Z",
};

const occurrences = [
  {
    appointment_id: 900,
    calendar_id: 1,
    title: "Sprint planning",
    description: "Plan next sprint",
    location: "Room 2",
    start_time: appointmentStart,
    end_time: appointmentEnd,
    all_day: false,
    is_recurring: false,
  },
];

const processSummaries = [
  { id: 1, entity_id: "Process-1", name: "Onboarding", state_entity_id: "S1" },
];

const processDetail = {
  id: 1,
  entity_id: "Process-1",
  name: "Onboarding",
  state_entity_id: "S1",
  start_activity_entity_id: "Activity-a",
  activities: [
    {
      entity_id: "Activity-a",
      name: "Collect info",
      activity_dialogs: [{ entity_id: "ActivityDialog-1", name: "Set Title" }],
    },
    { entity_id: "Activity-b", name: "Done", activity_dialogs: [] },
  ],
};

const activityDialogDetail = {
  entity_id: "ActivityDialog-1",
  name: "Set Title",
  description_short: "Set the title",
  description_long: "Fill in the ticket title.",
  field_order: ["Title"],
  fields: {
    Title: {
      display: "1",
      default_value: "",
      description_short: "Title",
      description_long: "Ticket title",
      config: {},
    },
  },
  submit_advice_text: "",
  submit_button_text: "Submit",
};

// The shared dialog form (start dialog and activity dialog answer the same shape).
const processDialogForm: ProcessDialogFormData = {
  process_entity_id: "Process-1",
  activity_dialog_entity_id: "ActivityDialog-1",
  name: "Set Title",
  description_short: "Set the title",
  description_long: "Fill in the ticket title.",
  submit_button_text: "Submit",
  submit_advice_text: "",
  fields: [
    {
      name: "Title",
      kind: "text",
      label: "Title",
      required: false,
      description_short: "Title",
      description_long: "Ticket title",
      default: "",
      options: [],
    },
  ],
};

const ticketProcessStateNotInProcess: TicketProcessState = {
  can_start: true,
  process_state: null,
  process_entity_id: null,
  process_name: null,
  activity_entity_id: null,
  activity_name: null,
  available_dialogs: [],
  available_transitions_count: 0,
};

const ticketProcessStateActive: TicketProcessState = {
  can_start: false,
  process_state: "Active",
  process_entity_id: "Process-1",
  process_name: "Onboarding",
  activity_entity_id: "Activity-a",
  activity_name: "Collect info",
  available_dialogs: [
    { entity_id: "ActivityDialog-1", name: "Set Title", description_short: "Set the title" },
  ],
  available_transitions_count: 1,
};

const ticketProcessStateAfterSubmit: TicketProcessState = {
  can_start: false,
  process_state: "Active",
  process_entity_id: "Process-1",
  process_name: "Onboarding",
  activity_entity_id: "Activity-b",
  activity_name: "Done",
  available_dialogs: [],
  available_transitions_count: 0,
};

// Mutated by the /start and /submit handlers below to simulate the ticket
// moving through the process — reset per-test in mockApi().
let ticketProcessState: TicketProcessState = ticketProcessStateNotInProcess;

let authenticated = false;

/**
 * Stateful dashboards store per test (R-DASH-E2E-STORE): the pages of one test share it, tests do
 * not - serial tests in one worker would otherwise inherit each other's saves. Seeded with the
 * default dashboard "Mein Start" plus a second dashboard "Alt" holding one widget of an unknown type.
 */
/** The "Leitstand" board of the e2e store and the wallboard screenshot: four titled KPIs, two lists. */
function wallboardWidgets() {
  const base = demoDefaultWidgets();
  const place = (id: string, title: string, x: number, y: number, w: number, h: number) => {
    const src = base.find((b) => b.id === id)!;
    return { ...src, id: `lb-${id}`, title_override: title, layout: { lg: { x, y, w, h } } };
  };
  const counters = [
    place("demo-w-open", "Offen", 0, 0, 3, 3),
    place("demo-w-new", "Neu", 3, 0, 3, 3),
    place("demo-w-unowned", "Zum Übernehmen", 6, 0, 3, 3),
    place("demo-w-escalated", "Eskaliert", 9, 0, 3, 3),
  ];
  return [
    ...counters,
    place("demo-w-escalations", "Nächste Fristen", 0, 3, 12, 4),
    place("demo-w-list-new", "Neue Tickets", 0, 7, 6, 5),
    place("demo-w-list-open", "Offene Tickets", 6, 7, 6, 5),
  ];
}

const dashboardStores = new Map<string, DemoDashboards>();
function dashboardStore(): DemoDashboards {
  const id = test.info().testId;
  let store = dashboardStores.get(id);
  if (!store) {
    store = createDemoDashboards({
      extraDashboards: [
        {
          name: "Alt",
          widgets: [
            {
              id: "e2e-gone",
              type_key: "org.example.gone",
              type_version: 1,
              title_override: null,
              settings: { keep: "me" },
              layout: { lg: { x: 0, y: 0, w: 6, h: 3 } },
            },
            {
              id: "e2e-gone-search",
              type_key: "core.ticket-list",
              type_version: 1,
              title_override: null,
              settings: { source: { kind: "saved_search", id: GONE_SAVED_SEARCH_ID }, limit: 8 },
              layout: { lg: { x: 6, y: 0, w: 6, h: 5 } },
            },
          ],
        },
        {
          // A copy of the role template "Support-Team" whose template has gained two widgets since.
          name: "Teamvorlage",
          source_template_id: 100,
          template_update: demoTemplateUpdateHint(),
          widgets: demoDefaultWidgets().slice(0, 4),
        },
        {
          // Sized for a screen: 12 rows (the link dialog warns above 14), titled tiles.
          name: "Leitstand",
          widgets: wallboardWidgets(),
        },
      ],
    });
    dashboardStores.set(id, store);
  }
  return store;
}

// --- saved reports / saved searches: in-memory per test (reset in mockApi) ------------------
// The seed rows are the demo's pure data; the store and its mutations live here so no test sees
// another test's writes.
const ME = { user_id: 1, name: "Ada Agent" };
let savedReports: ReportItem[] = [];
let nextReportId = 103;
let savedSearches: SavedSearch[] = [];
let nextSearchId = 14;
let znunyImported = new Set<string>();
// Report schedules: the demo's pure store; runs of a manual send are flipped queued -> running -> succeeded on the polls.
let scheduleStore: DemoSchedules = createDemoSchedules();
const PDF_EXPORT_JOB_ID = 9201;

/** The run list as a poll sees it: an active run advances one step per request (queued -> running -> succeeded). */
function advanceActiveRuns(scheduleId: number): void {
  for (const run of scheduleStore.runs(scheduleId)) {
    if (run.status === "queued") run.status = "running";
    else if (run.status === "running" && run.id > 15) {
      run.status = "succeeded";
      run.started_at = "2026-10-11T09:00:01Z";
      run.finished_at = "2026-10-11T09:00:04Z";
      run.recipient_count = 1;
      run.row_count = 42;
    }
  }
}

/** Znuny preview of the e2e: the demo's two profiles plus one that maps to nothing. */
function znunyPreview(): ZnunyImportPreview {
  const demo = demoZnunyPreview().profiles;
  return {
    profiles: [
      ...demo.map((p) => ({ ...p, existing_id: znunyImported.has(p.name) ? 1 : null })),
      {
        name: "Volltext",
        criteria: { version: 1 },
        include_archived: false,
        existing_id: null,
        importable: false,
        partial: false,
        unmapped: [{ key: "Fulltext", value: "Drucker", reason: "Volltextsuche wird nicht unterstützt." }],
      },
    ],
  };
}

function resetSavedStore(): void {
  savedReports = demoSavedReports();
  nextReportId = 103;
  savedSearches = demoSavedSearches();
  nextSearchId = 14;
  znunyImported = new Set();
  scheduleStore = createDemoSchedules();
}

function applyReportBody(r: ReportItem, body: SavedReportIn | SavedReportUpdateIn): ReportItem {
  return {
    ...r,
    name: body.name,
    description: body.description,
    definition: body.definition,
    visibility: body.visibility,
    group_ids: body.visibility === "groups" ? (body.group_ids ?? []) : [],
    copied_from: body.copied_from ?? r.copied_from ?? null,
  };
}

function listParamCriteria(listParams: SavedSearchIn["list_params"]): SavedSearch["criteria"] {
  const stateType = listParams?.state_type;
  if (typeof stateType !== "string" || stateType === "") return { version: 1 };
  return { version: 1, where: { all: [{ field: "state_type", op: "in", value: [stateType] }] } };
}

function newSearch(body: SavedSearchIn): SavedSearch {
  return {
    ...demoSavedSearches()[0],
    id: nextSearchId++,
    name: body.name,
    criteria: body.criteria ?? listParamCriteria(body.list_params),
    group_ids: body.group_ids ?? [],
    include_archived: body.include_archived,
    show_in_sidebar: body.show_in_sidebar,
    show_count: body.show_count,
    sort_by: body.sort_by,
    sort_order: body.sort_order,
    owner: ME,
    is_owner: true,
    can_edit: true,
    position: savedSearches.length + 1,
    source: "tiqora",
    source_name: null,
    version: 1,
  };
}

async function json(route: Route, status: number, body: unknown) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

// The test whose run last reset the module-level mock state (session flag, KB article, process
// state, saved searches).
let stateResetFor: TestInfo | undefined;
// CMDB demo store (two classes, six CIs); reset with the rest of the per-test state.
let cmdbStore: DemoCmdb = createDemoCmdb();

/**
 * Intercept all /api/v1/* calls with deterministic fixtures.
 * Call it for every page of a test (beforeEach plus any extra page). The shared mock state is reset
 * only on the first call of a test: a reset from a second page's call would sign out the pages that
 * are already open (their next request gets 401 and the app sends them to /login).
 */
export async function mockApi(page: Page) {
  if (stateResetFor !== test.info()) {
    stateResetFor = test.info();
    authenticated = false;
    kbArticleFull = initialKbArticle();
    ticketProcessState = ticketProcessStateNotInProcess;
    resetSavedStore();
    cmdbStore = createDemoCmdb();
  }

  await page.route("**/api/v1/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();

    // Wallboard (public, header-authenticated): redacted view of the same per-test store, with the
    // default link setting (details off), so the derived-preset titles are what the e2e sees.
    if (path.includes("/api/v1/kiosk/")) {
      const out = resolveKiosk(dashboardStore(), method, path, req.headers()["x-tiqora-kiosk"]);
      if (out) {
        await json(route, out.status, out.body);
        return;
      }
    }

    // Dashboards (/agent home page): stateful per test, answered from the request body/query.
    if (/\/api\/v1\/(dashboards|dashboard-widget-types|admin\/dashboard-templates|admin\/dashboard-kiosk-links)(\/|$)/.test(path)) {
      const hasBody = method !== "GET" && method !== "DELETE";
      const out = resolveDashboards(dashboardStore(), method, path, hasBody ? req.postDataJSON() : undefined, url.searchParams);
      if (out) {
        if (out.status === 204) await route.fulfill({ status: 204 });
        else await json(route, out.status, out.body);
        return;
      }
    }

    // Plugin list of the agent/admin shells: no plugins installed.
    if (path.endsWith("/api/v1/plugins/frontend") && method === "GET") {
      await json(route, 200, []);
      return;
    }

    // Auth
    if (path.endsWith("/api/v1/auth/methods") && method === "GET") {
      await json(route, 200, {
        password: true,
        oidc: false,
        spnego: false,
        ldap: false,
        webauthn: false,
        portal_enabled: true,
      });
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

    // CMDB (Mode B): in-memory store of the demo mock.
    if (/\/api\/v1\/(admin\/)?cmdb(\/|$)/.test(path)) {
      const hasBody = method !== "GET" && method !== "DELETE" && !(req.headers()["content-type"] ?? "").includes("multipart");
      const out = resolveCmdb(cmdbStore, method, path, hasBody ? req.postDataJSON() : undefined, url.searchParams);
      if (out) {
        if (out.status === 204) await route.fulfill({ status: 204 });
        else await json(route, out.status, out.body);
        return;
      }
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

    // Header counters (@ / ⏱) query these on every ticket zoom.
    if (path.match(/\/api\/v1\/tickets\/\d+\/mentions$/) && method === "GET") {
      await json(route, 200, [
        { id: 1, ticket_id: 1, user_id: 2, user_name: "Ada Lovelace", user_login: "ada" },
      ]);
      return;
    }
    if (path.match(/\/api\/v1\/tickets\/\d+\/mentions$/) && method === "POST") {
      await json(route, 201, { id: 2, ticket_id: 1, user_id: 3 });
      return;
    }
    if (path.match(/\/api\/v1\/tickets\/\d+\/time-accounting$/) && method === "GET") {
      await json(route, 200, [
        { id: 1, ticket_id: 1, time_unit: 15, create_by: 2, create_by_login: "ada" },
      ]);
      return;
    }
    if (path.match(/\/api\/v1\/tickets\/\d+\/time-accounting$/) && method === "POST") {
      await json(route, 201, { id: 2, ticket_id: 1, time_unit: 5, create_by: 2 });
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
    if (path.match(/\/api\/v1\/tickets\/\d+\/articles\/\d+\/reply-draft$/) && method === "GET") {
      await json(route, 200, {
        subject: "Re: Printer smoke on level 3",
        body: "> quoted original",
        is_html: false,
        to_address: "customer@example.com",
        cc: "",
        in_reply_to: null,
        references: null,
        signature: "",
        signature_is_html: false,
      });
      return;
    }
    if (path.match(/\/api\/v1\/tickets\/\d+\/drafts$/) && method === "GET") {
      await json(route, 200, []);
      return;
    }
    if (path.match(/\/api\/v1\/tickets\/\d+\/templates$/) && method === "GET") {
      await json(route, 200, []);
      return;
    }
    // Composer auto-lock (Znuny RequiredLock). Ticket 101 plays "locked by
    // another agent" until a takeover is posted; everything else acquires.
    if (path.match(/\/api\/v1\/tickets\/\d+\/acquire-lock$/) && method === "POST") {
      const req_ = route.request().postDataJSON() as { takeover?: boolean };
      if (path.includes("/tickets/101/") && !req_.takeover) {
        await json(route, 200, {
          result: "locked_by_other",
          locked_by_id: 7,
          locked_by_name: "Bea Blocker",
        });
        return;
      }
      await json(route, 200, {
        result: req_.takeover ? "taken_over" : "acquired",
        locked_by_id: null,
        locked_by_name: null,
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
    if (path.match(/\/api\/v1\/tickets\/(\d+)$/)) {
      // Echo the requested id so per-ticket behaviour (e.g. the foreign-lock
      // fixture on ticket 101) sees consistent ids across all endpoints.
      const id = Number(path.match(/\/api\/v1\/tickets\/(\d+)$/)?.[1] ?? 100);
      await json(route, 200, { ...ticketDetail, id });
      return;
    }
    if (path.endsWith("/api/v1/tickets") && method === "GET") {
      await json(route, 200, tickets);
      return;
    }
    if (path.endsWith("/api/v1/tickets/facets") && method === "GET") {
      await json(route, 200, {
        states: { todo: 2, new: 1, open_only: 1, pending: 0, closed: 0, all: 2 },
        flags: { escalated: 0, locked: 0, unassigned: 1 },
      });
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

    // Reports (/agent/reports): catalogue + saved reports, one definition, runs answered from the request body.
    if (path.endsWith("/api/v1/reports") && method === "GET") {
      await json(route, 200, { ...DEMO_REPORT_CATALOGUE, reports: [...DEMO_REPORT_CATALOGUE.reports, ...savedReports] });
      return;
    }
    if (path.endsWith("/api/v1/reports") && method === "POST") {
      const id = nextReportId++;
      const made = applyReportBody(
        { ...demoSavedReports()[0], key: String(id), id, owner: ME, version: 1, copied_from: null },
        req.postDataJSON() as SavedReportIn,
      );
      savedReports = [...savedReports, made];
      await json(route, 201, made);
      return;
    }
    if (path.endsWith("/api/v1/reports/run") && method === "POST") {
      const body = req.postDataJSON() as ReportRunIn;
      const saved = savedReports.find((r) => r.key === body.report_key);
      // A saved key runs as its stored definition (the demo resolver only knows built-ins).
      const run = saved?.definition ? { ...body, report_key: undefined, definition: saved.definition } : body;
      await json(route, 200, resolveReportRun(run));
      return;
    }
    const savedReportAt = path.match(/\/api\/v1\/reports\/(\d+)$/);
    if (savedReportAt) {
      const found = savedReports.find((r) => r.key === savedReportAt[1]);
      if (!found) {
        await json(route, 404, { detail: "Bericht nicht gefunden." });
        return;
      }
      if (method === "GET") {
        await json(route, 200, found);
        return;
      }
      if (method === "PUT") {
        const next = { ...applyReportBody(found, req.postDataJSON() as SavedReportUpdateIn), version: (found.version ?? 1) + 1 };
        savedReports = savedReports.map((r) => (r.key === found.key ? next : r));
        await json(route, 200, next);
        return;
      }
      if (method === "DELETE") {
        savedReports = savedReports.filter((r) => r.key !== found.key);
        await route.fulfill({ status: 204, body: "" });
        return;
      }
    }
    if (path.endsWith("/api/v1/reference/share-groups") && method === "GET") {
      await json(route, 200, DEMO_SHARE_GROUPS);
      return;
    }

    // Saved searches, the structured ticket query and the filter helpers.
    if (path.endsWith("/api/v1/saved-searches") && method === "GET") {
      await json(route, 200, savedSearches);
      return;
    }
    if (path.endsWith("/api/v1/saved-searches") && method === "POST") {
      const made = newSearch(req.postDataJSON() as SavedSearchIn);
      savedSearches = [...savedSearches, made];
      await json(route, 201, made);
      return;
    }
    if (path.endsWith("/api/v1/saved-searches/counts") && method === "GET") {
      const counts: Record<string, number> = {};
      for (const s of savedSearches) if (s.show_in_sidebar && s.show_count) counts[String(s.id)] = 5;
      await json(route, 200, { counts });
      return;
    }
    if (path.endsWith("/api/v1/saved-searches/import/znuny") && method === "GET") {
      await json(route, 200, znunyPreview());
      return;
    }
    if (path.endsWith("/api/v1/saved-searches/import/znuny") && method === "POST") {
      const names = (req.postDataJSON() as { names: string[] }).names;
      const result: ZnunyImportResult = { created: [], updated: [], skipped: [] };
      for (const name of names) {
        const profile = znunyPreview().profiles.find((p) => p.name === name);
        if (!profile || !profile.importable) {
          result.skipped.push({ name, reason: "Nichts übernehmbar." });
          continue;
        }
        const made: SavedSearch = {
          ...newSearch({
            name,
            criteria: profile.criteria,
            include_archived: profile.include_archived,
            show_in_sidebar: false,
            show_count: false,
            sort_by: "changed",
            sort_order: "desc",
          }),
          source: "znuny",
          source_name: name,
        };
        savedSearches = [...savedSearches, made];
        znunyImported.add(name);
        result.created.push(made.id);
      }
      await json(route, 200, result);
      return;
    }
    const searchAt = path.match(/\/api\/v1\/saved-searches\/(\d+)(\/prefs)?$/);
    if (searchAt) {
      const id = Number(searchAt[1]);
      const found = savedSearches.find((s) => s.id === id);
      if (!found) {
        await json(route, 404, { detail: "Suche nicht gefunden." });
        return;
      }
      if (method === "GET") {
        await json(route, 200, found);
        return;
      }
      let next: SavedSearch | null = null;
      if (method === "PUT" && searchAt[2]) {
        const prefs = req.postDataJSON() as SavedSearchPrefsIn;
        next = { ...found, show_in_sidebar: prefs.show_in_sidebar, show_count: prefs.show_count, position: prefs.position ?? found.position };
      } else if (method === "PUT") {
        const body = req.postDataJSON() as SavedSearchUpdateIn;
        next = {
          ...found,
          name: body.name,
          criteria: body.criteria ?? found.criteria,
          group_ids: body.group_ids ?? [],
          include_archived: body.include_archived,
          show_in_sidebar: body.show_in_sidebar,
          show_count: body.show_count,
          sort_by: body.sort_by,
          sort_order: body.sort_order,
          version: found.version + 1,
        };
      } else if (method === "DELETE") {
        savedSearches = savedSearches.filter((s) => s.id !== id);
        await route.fulfill({ status: 204, body: "" });
        return;
      }
      if (next) {
        const updated = next;
        savedSearches = savedSearches.map((s) => (s.id === id ? updated : s));
        await json(route, 200, updated);
        return;
      }
    }
    if (path.endsWith("/api/v1/tickets/query") && method === "POST") {
      await json(route, 200, tickets);
      return;
    }
    if (path.endsWith("/api/v1/ticket-filters/count") && method === "POST") {
      await json(route, 200, { count: tickets.items.length });
      return;
    }
    if (path.endsWith("/api/v1/ticket-filters/fields") && method === "GET") {
      await json(route, 200, demoTicketFilterFields());
      return;
    }

    if (path.endsWith("/api/v1/reports/export") && method === "POST") {
      await json(route, 202, { job_id: PDF_EXPORT_JOB_ID, status: "queued" });
      return;
    }
    if (path.endsWith(`/api/v1/jobs/${PDF_EXPORT_JOB_ID}`) && method === "GET") {
      await json(route, 200, {
        id: PDF_EXPORT_JOB_ID,
        kind: "core.report_export",
        title_key: "jobs.kinds.core.report_export",
        status: "succeeded",
        progress_done: 1,
        progress_total: 1,
        progress_message: null,
        cancel_requested: false,
        result: null,
        error: null,
        files: [{ id: 1, name: "bericht.pdf", content_type: "application/pdf", size_bytes: 20480 }],
        created_by: 1,
        created_at: "2026-10-11T09:00:00Z",
        started_at: "2026-10-11T09:00:01Z",
        finished_at: "2026-10-11T09:00:02Z",
      });
      return;
    }

    // Report schedules and the Znuny stats import (the demo's router; stateful per test).
    if (/\/api\/v1\/(report-schedules|reports\/import\/znuny)/.test(path)) {
      const hasBody = method !== "GET" && method !== "DELETE";
      const runsAt = path.match(/\/report-schedules\/(\d+)\/runs$/);
      if (runsAt && method === "GET") advanceActiveRuns(Number(runsAt[1]));
      const out = resolveSchedules(scheduleStore, method, path, hasBody ? req.postDataJSON() : undefined, url.searchParams);
      if (out) {
        if (path.endsWith("/import/znuny/apply") && method === "POST") {
          // The saved report the import made must be openable through the result link.
          const ids = (req.postDataJSON() as { stat_ids?: number[] }).stat_ids ?? [];
          for (const item of demoZnunyApply(ids).imported.filter((i) => i.target === "report")) {
            if (savedReports.some((r) => r.key === String(item.id))) continue;
            savedReports = [
              ...savedReports,
              { ...demoSavedReports()[0], key: String(item.id), id: item.id, name: item.title, owner: ME, version: 1, copied_from: null },
            ];
          }
        }
        if (out.status === 204) await route.fulfill({ status: 204 });
        else await json(route, out.status, out.body);
        return;
      }
    }
    if (path.match(/\/api\/v1\/reports\/builtin\.[a-z_]+$/) && method === "GET") {
      const report = demoReport(decodeURIComponent(path.split("/").pop() as string));
      await json(route, report ? 200 : 404, report ?? { detail: "Not found" });
      return;
    }
    if (path.endsWith("/api/v1/reporting/sources") && method === "GET") {
      await json(route, 200, demoSources());
      return;
    }
    if (path.endsWith("/api/v1/reporting/status") && method === "GET") {
      await json(route, 200, DEMO_FACTS);
      return;
    }

    if (/\/api\/v1\/admin\/webservices\/\d+\/debugger$/.test(path) && method === "GET") {
      await json(route, 200, filterDebugEntries(url.searchParams));
      return;
    }
    // GenericInterface webservices (list, detail, egress settings, dry run + its job)
    if (path.includes("/api/v1/admin/webservices") || path.endsWith("/api/v1/jobs/9390")) {
      const gi = resolveGiData(path, method);
      if (gi !== undefined) {
        await json(route, method === "POST" ? 202 : 200, gi);
        return;
      }
    }

    // Calendar
    if (path.endsWith("/api/v1/calendar/calendars") && method === "GET") {
      await json(route, 200, calendars);
      return;
    }
    if (path.endsWith("/api/v1/calendar/appointments") && method === "GET") {
      await json(route, 200, occurrences);
      return;
    }
    if (path.endsWith("/api/v1/calendar/appointments") && method === "POST") {
      const body = req.postDataJSON() as Record<string, unknown>;
      await json(route, 201, {
        id: 900,
        parent_id: null,
        unique_id: "900@tiqora",
        description: null,
        location: null,
        all_day: false,
        team_id: null,
        resource_id: null,
        recurring: false,
        recur_type: null,
        recur_interval: null,
        recur_count: null,
        recur_until: null,
        create_time: "2026-07-19T09:00:00Z",
        change_time: "2026-07-19T09:00:00Z",
        ...body,
      });
      return;
    }
    if (path.match(/\/api\/v1\/calendar\/appointments\/\d+\/tickets$/)) {
      await json(route, 200, []);
      return;
    }
    if (path.match(/\/api\/v1\/calendar\/appointments\/\d+$/) && method === "GET") {
      await json(route, 200, calendarAppointment);
      return;
    }
    if (path.match(/\/api\/v1\/calendar\/appointments\/\d+$/) && method === "PATCH") {
      const body = req.postDataJSON() as Record<string, unknown>;
      await json(route, 200, { ...calendarAppointment, ...body });
      return;
    }
    if (path.match(/\/api\/v1\/calendar\/appointments\/\d+$/) && method === "DELETE") {
      await route.fulfill({ status: 204, body: "" });
      return;
    }

    // ProcessManagement (BPM)
    if (path.match(/\/api\/v1\/process\/ticket\/\d+\/state$/) && method === "GET") {
      await json(route, 200, ticketProcessState);
      return;
    }
    if (path.match(/\/api\/v1\/process\/ticket\/\d+\/start$/) && method === "POST") {
      ticketProcessState = ticketProcessStateActive;
      await json(route, 200, ticketProcessState);
      return;
    }
    if (path.match(/\/api\/v1\/process\/ticket\/\d+\/submit$/) && method === "POST") {
      ticketProcessState = ticketProcessStateAfterSubmit;
      await json(route, 200, {
        activity_changed: true,
        new_activity_entity_id: "Activity-b",
        transition_entity_id: "Transition-1",
        unsupported_actions: [],
        state: ticketProcessState,
      });
      return;
    }
    if (
      (path.match(/\/api\/v1\/process\/ticket\/\d+\/dialog\/[^/]+\/form$/) ||
        path.match(/\/api\/v1\/process\/[^/]+\/start-form$/)) &&
      method === "GET"
    ) {
      await json(route, 200, processDialogForm);
      return;
    }
    if (path.match(/\/api\/v1\/process\/.*\/options$/) && method === "POST") {
      await json(route, 200, { options: {} });
      return;
    }
    if (path.match(/\/api\/v1\/process\/ticket\/\d+\/path$/) && method === "GET") {
      await json(route, 200, { process_entity_id: "Process-1", steps: [] });
      return;
    }
    if (path.match(/\/api\/v1\/process\/activity-dialog\/[^/]+$/) && method === "GET") {
      await json(route, 200, activityDialogDetail);
      return;
    }
    if (path.endsWith("/api/v1/process/") && method === "GET") {
      await json(route, 200, processSummaries);
      return;
    }
    if (path.match(/\/api\/v1\/process\/[^/]+$/) && method === "GET") {
      await json(route, 200, processDetail);
      return;
    }

    await json(route, 404, { detail: `No mock for ${method} ${path}` });
  });
}

/** German UI copy: the app reads its language from localStorage, not from the browser locale. */
export async function useGermanUi(page: Page) {
  await page.addInitScript(() => {
    try {
      localStorage.setItem("tiqora-lang", "de");
    } catch {
      /* no storage: English copy */
    }
  });
}

export async function loginAsAgent(page: Page) {
  await page.goto("/login");
  await page.getByTestId("login-username").fill("agent");
  await page.getByTestId("login-password").fill("secret");
  await page.getByTestId("login-submit").click();
  await page.waitForURL(/\/agent/);
}
