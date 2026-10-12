import type { Page, Route } from "@playwright/test";
import { mockApi, loginAsAgent } from "./mock-api";
import type { AdminProcess, DeployStatus } from "../../src/lib/deployStatusApi";

async function json(route: Route, status: number, body: unknown) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

/**
 * Admin list endpoints return a paginated envelope (`Page[Out]` on the
 * backend) — `AdminResourcePage` reads `.items`/`.total`. Wrap the in-memory
 * arrays so the tables render rows. Non-paginated admin GETs (system
 * addresses, ACL, generic-agent jobs, membership sub-resources) stay bare
 * arrays via the catch-all below.
 */
function pageOf<T>(items: T[]) {
  return { items, total: items.length, page: 1, page_size: items.length || 25 };
}

function initialUsers() {
  return [
    {
      id: 1,
      login: "agent",
      title: null,
      first_name: "Ada",
      last_name: "Agent",
      valid_id: 1,
      create_time: "2026-07-01T00:00:00Z",
      change_time: "2026-07-01T00:00:00Z",
    },
  ];
}

function initialGroups() {
  return [
    {
      id: 1,
      name: "admin",
      comments: "Administrators",
      valid_id: 1,
      create_time: "2026-07-01T00:00:00Z",
      change_time: "2026-07-01T00:00:00Z",
    },
  ];
}

function initialQueues() {
  return [
    {
      id: 1,
      name: "Raw",
      group_id: 1,
      unlock_timeout: null,
      first_response_time: null,
      first_response_notify: null,
      update_time: null,
      update_notify: null,
      solution_time: null,
      solution_notify: null,
      system_address_id: 1,
      calendar_name: null,
      default_sign_key: null,
      salutation_id: 1,
      signature_id: 1,
      follow_up_id: 1,
      follow_up_lock: 0,
      comments: null,
      valid_id: 1,
      create_time: "2026-07-01T00:00:00Z",
      change_time: "2026-07-01T00:00:00Z",
    },
  ];
}

function initialDynamicFields() {
  return [
    {
      id: 1,
      internal_field: 0,
      name: "Process",
      label: "Process",
      field_order: 1,
      field_type: "Text",
      object_type: "Ticket",
      config: {},
      valid_id: 1,
      create_time: "2026-07-01T00:00:00Z",
      change_time: "2026-07-01T00:00:00Z",
    },
  ];
}

let users = initialUsers();
let groups = initialGroups();
let queues = initialQueues();
let dynamicFields = initialDynamicFields();
let nextId = 1000;

// --- Process designer: a small stateful stand-in for /admin/process-designer, /admin/deploy*. ---

type DesignerEntity = {
  entity_type: string;
  entity_id: string;
  id: number;
  name: string;
  config: Record<string, unknown>;
  version: string;
  sync_state: "synced" | "not_sync";
  changed_at: string;
  changed_by: number;
};
type DesignerProcess = {
  process: DesignerEntity;
  state_entity_id: string;
  layout: Record<string, Record<string, string | number>>;
};
/** Union of the request bodies the mock reads (process create/update, entity create/update). */
type DesignerBody = {
  name: string;
  description: string;
  expected_version: string;
  config: Record<string, unknown>;
  path: Record<string, unknown>;
  layout: Record<string, Record<string, string | number>>;
  start_activity: string;
  start_activity_dialog: string;
  state_entity_id: string;
};
type SyncState = "not_needed" | "pending" | "ok";

const STATE_NAMES: Record<string, string> = { S1: "Active", S2: "Inactive", S3: "FadeAway" };
/** Znuny applies a deployment a moment after the signal; the mock reports "ok" after this long. */
const ADDON_DELAY_MS = 3000;

const designerEntity = (
  entity_type: string,
  entity_id: string,
  id: number,
  name: string,
  config: Record<string, unknown>,
): DesignerEntity => ({
  entity_type,
  entity_id,
  id,
  name,
  config,
  version: `v${id}.0`,
  sync_state: "synced",
  changed_at: "2026-10-09T09:00:00Z",
  changed_by: 1,
});

function initialDesigner() {
  return {
    seq: 100,
    processes: new Map<string, DesignerProcess>([
      [
        "Process-1",
        {
          process: designerEntity("Process", "Process-1", 1, "Onboarding", {
            Description: "",
            StartActivity: "Activity-a",
            StartActivityDialog: "ActivityDialog-1",
            Path: { "Activity-a": { "Transition-1": { ActivityEntityID: "Activity-b" } }, "Activity-b": {} },
          }),
          state_entity_id: "S1",
          layout: { "Activity-a": { left: "40", top: "40" }, "Activity-b": { left: "300", top: "40" } },
        },
      ],
    ]),
    entities: {
      Activity: [designerEntity("Activity", "Activity-a", 1, "Collect info", {}), designerEntity("Activity", "Activity-b", 2, "Done", {})],
      ActivityDialog: [] as DesignerEntity[],
      Transition: [designerEntity("Transition", "Transition-1", 1, "Go on", {})],
      TransitionAction: [] as DesignerEntity[],
    } as Record<string, DesignerEntity[]>,
    pending: 0,
    revision: null as null | { id: number; source: string; entity_count: number; deployed_at: string; deployed_by: number },
    sync: "not_needed" as SyncState,
    deployedAt: 0,
  };
}

let designer = initialDesigner();

const designerCatalog = {
  actions: [
    {
      name: "TicketStateSet",
      module: "Kernel::System::ProcessManagement::TransitionAction::TicketStateSet",
      supported: true,
      tiqora_only: false,
      params: [
        { name: "State", kind: "state", required: true, one_of: ["State", "StateID"], list_value: false },
        { name: "StateID", kind: "state", required: true, one_of: ["State", "StateID"], list_value: false },
      ],
    },
  ],
  condition_types: ["String", "Regexp"],
  condition_modules: [],
  fields: [
    { name: "Title", label: "Title", kind: "text", dialog: true, condition: true, options: [] },
    {
      name: "Queue",
      label: "Queue",
      kind: "select",
      dialog: true,
      condition: true,
      options: [
        { value: "Raw", label: "Raw" },
        { value: "Support", label: "Support" },
      ],
    },
  ],
  interfaces: ["AgentInterface"],
  permissions: [""],
  account_time: false,
};

function draftOf(eid: string) {
  const p = designer.processes.get(eid);
  if (!p) return null;
  const e = designer.entities;
  return {
    process: p.process,
    state_entity_id: p.state_entity_id,
    state_name: STATE_NAMES[p.state_entity_id] ?? "",
    layout: p.layout,
    activities: e.Activity,
    activity_dialogs: e.ActivityDialog,
    transitions: e.Transition,
    transition_actions: e.TransitionAction,
    missing_references: [],
    pending_changes: designer.pending,
  };
}

function summaryOf(p: DesignerProcess) {
  return {
    entity_type: "Process",
    entity_id: p.process.entity_id,
    name: p.process.name,
    used_by: [],
    sync_state: p.process.sync_state,
    state_entity_id: p.state_entity_id,
    state_name: STATE_NAMES[p.state_entity_id] ?? "",
  };
}

function deployStatus(): DeployStatus {
  // Znuny's daemon picks the signal up after a short delay.
  if (designer.sync === "pending" && Date.now() - designer.deployedAt >= ADDON_DELAY_MS) designer.sync = "ok";
  const idle = { pending_changes: 0, revision: null, running_live: true, deploy_mode: null, znuny_sync: { state: "not_needed" as const } };
  return {
    acl: idle,
    process: {
      pending_changes: designer.pending,
      revision: designer.revision,
      running_live: designer.revision === null,
      deploy_mode: "signal",
      znuny_sync: { state: designer.sync, overdue: false, message: "" },
    },
    can_deploy_from_tiqora: false,
    ownership_active: false,
  };
}

/** Routes of the designer and the deploy status; returns false for anything else. */
async function handleProcessDesigner(route: Route, path: string, method: string): Promise<boolean> {
  const req = route.request();
  const send = async (status: number, payload: unknown) => {
    await json(route, status, payload);
    return true;
  };
  if (path.endsWith("/api/v1/admin/deploy-status") && method === "GET") {
    return send(200, deployStatus());
  }
  if (path.endsWith("/api/v1/admin/deploy") && method === "POST") {
    const processes = [...designer.processes.values()];
    for (const p of processes) p.process.sync_state = "synced";
    for (const list of Object.values(designer.entities)) for (const x of list) x.sync_state = "synced";
    designer.pending = 0;
    designer.sync = "pending";
    designer.deployedAt = Date.now();
    designer.revision = {
      id: designer.seq++,
      source: "tiqora",
      entity_count: processes.length + Object.values(designer.entities).flat().length,
      deployed_at: new Date().toISOString(),
      deployed_by: 1,
    };
    return send(200, {
      kind: "process",
      revision_id: designer.revision.id,
      checksum: "e2e",
      action: "recorded",
      mode: "signal",
    });
  }
  const base = "/api/v1/admin/process-designer/";
  if (!path.includes(base)) return false;
  const rest = path.slice(path.indexOf(base) + base.length);
  const seg = rest.split("/").map(decodeURIComponent);
  const body = () => req.postDataJSON() as DesignerBody;
  const touch = (x: DesignerEntity) => {
    x.version = `v${x.id}.${++designer.seq}`;
    x.sync_state = "not_sync";
    designer.pending += 1;
  };

  if (seg[0] === "catalog") return send(200, designerCatalog);
  if (seg[0] === "revisions") return send(200, []);
  if (seg[0] === "entities" && method === "GET") {
    return send(200, (designer.entities[seg[1]] ?? []).map((x) => ({ ...x, used_by: [] })));
  }

  if (seg[0] === "processes") {
    const eid = seg[1];
    if (!eid) {
      if (method === "GET") return send(200, [...designer.processes.values()].map(summaryOf));
      const b = body();
      const id = ++designer.seq;
      const p: DesignerProcess = {
        process: designerEntity("Process", `Process-${id}`, id, b.name, {
          Description: b.description ?? "",
          StartActivity: "",
          StartActivityDialog: "",
          Path: {},
        }),
        state_entity_id: "S2",
        layout: {},
      };
      touch(p.process);
      designer.processes.set(p.process.entity_id, p);
      return send(201, draftOf(p.process.entity_id));
    }
    const p = designer.processes.get(eid);
    if (!p) return send(404, { detail: "Prozess nicht gefunden" });
    if (seg[2] === "validation") return send(200, []);
    if (seg[2] === "export") {
      await route.fulfill({
        status: 200,
        contentType: "text/yaml",
        headers: { "content-disposition": `attachment; filename="${eid}.yml"` },
        body: `---\nProcess:\n- EntityID: ${eid}\n  Name: ${p.process.name}\n`,
      });
      return true;
    }
    if (method === "GET") return send(200, draftOf(eid));
    if (method === "PUT") {
      const b = body();
      if (b.expected_version !== p.process.version) {
        return send(409, { detail: "Der Prozess wurde inzwischen geändert." });
      }
      p.process.name = b.name;
      p.process.config = {
        ...p.process.config,
        Description: b.description,
        Path: b.path,
        StartActivity: b.start_activity,
        StartActivityDialog: b.start_activity_dialog,
      };
      p.layout = b.layout;
      p.state_entity_id = b.state_entity_id;
      touch(p.process);
      return send(200, draftOf(eid));
    }
  }

  if (seg[0] === "entities" && designer.entities[seg[1]]) {
    const list = designer.entities[seg[1]];
    if (method === "POST") {
      const b = body();
      const id = ++designer.seq;
      const x = designerEntity(seg[1], `${seg[1]}-${id}`, id, b.name, b.config);
      touch(x);
      list.push(x);
      return send(201, x);
    }
    const x = list.find((e) => e.entity_id === seg[2]);
    if (!x) return send(404, { detail: "Eintrag nicht gefunden" });
    if (method === "PUT") {
      const b = body();
      if (b.expected_version !== x.version) return send(409, { detail: "Geändert." });
      x.name = b.name;
      x.config = b.config;
      touch(x);
      return send(200, x);
    }
  }
  return send(404, { detail: `No designer mock for ${method} ${path}` });
}

function resetAdminState() {
  designer = initialDesigner();
  users = initialUsers();
  groups = initialGroups();
  queues = initialQueues();
  dynamicFields = initialDynamicFields();
  nextId = 1000;
}

/**
 * Extends mockApi with `/api/v1/admin/**` fixtures: users, groups, queues,
 * dynamic fields (in-memory CRUD) for the admin resource pages. Admin access
 * itself is gated by ``UserMe.is_admin`` on the default mock user (true).
 */
export async function mockAdminApi(page: Page) {
  resetAdminState();
  await mockApi(page);

  await page.route("**/api/v1/admin/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();

    if (await handleProcessDesigner(route, path, method)) return;

    // ProcessManagement overview (read-only)
    if (path.endsWith("/api/v1/admin/processes") && method === "GET") {
      const processes: AdminProcess[] = [
        {
          entity_id: "Process-1",
          name: "Onboarding",
          state_entity_id: "S1",
          state_name: "Active",
          activity_count: 2,
          start_interfaces: ["AgentInterface"],
        },
      ];
      await json(route, 200, processes);
      return;
    }

    // Users
    if (path.endsWith("/api/v1/admin/users") && method === "GET") {
      await json(route, 200, pageOf(users));
      return;
    }
    if (path.endsWith("/api/v1/admin/users") && method === "POST") {
      const body = req.postDataJSON() as Record<string, unknown>;
      const created = {
        id: ++nextId,
        login: body.login,
        title: body.title ?? null,
        first_name: body.first_name,
        last_name: body.last_name,
        valid_id: body.valid_id ?? 1,
        create_time: "2026-07-19T00:00:00Z",
        change_time: "2026-07-19T00:00:00Z",
      };
      users = [...users, created];
      await json(route, 201, created);
      return;
    }
    const userMatch = path.match(/\/api\/v1\/admin\/users\/(\d+)$/);
    if (userMatch && method === "PATCH") {
      const id = Number(userMatch[1]);
      const body = req.postDataJSON() as Record<string, unknown>;
      users = users.map((u) => (u.id === id ? { ...u, ...body, change_time: "2026-07-19T00:00:00Z" } : u));
      await json(route, 200, users.find((u) => u.id === id));
      return;
    }
    if (userMatch && method === "DELETE") {
      const id = Number(userMatch[1]);
      users = users.map((u) => (u.id === id ? { ...u, valid_id: 2 } : u));
      await route.fulfill({ status: 204, body: "" });
      return;
    }

    // Groups
    if (path.endsWith("/api/v1/admin/groups") && method === "GET") {
      await json(route, 200, pageOf(groups));
      return;
    }

    // Queues
    if (path.endsWith("/api/v1/admin/queues") && method === "GET") {
      await json(route, 200, pageOf(queues));
      return;
    }
    if (path.endsWith("/api/v1/admin/queues") && method === "POST") {
      const body = req.postDataJSON() as Record<string, unknown>;
      const created = {
        id: ++nextId,
        name: body.name,
        group_id: body.group_id,
        unlock_timeout: body.unlock_timeout ?? null,
        first_response_time: null,
        first_response_notify: null,
        update_time: null,
        update_notify: null,
        solution_time: null,
        solution_notify: null,
        system_address_id: body.system_address_id,
        calendar_name: null,
        default_sign_key: null,
        salutation_id: body.salutation_id,
        signature_id: body.signature_id,
        follow_up_id: body.follow_up_id,
        follow_up_lock: body.follow_up_lock ?? 0,
        comments: body.comments ?? null,
        valid_id: body.valid_id ?? 1,
        create_time: "2026-07-19T00:00:00Z",
        change_time: "2026-07-19T00:00:00Z",
      };
      queues = [...queues, created];
      await json(route, 201, created);
      return;
    }

    // Dynamic fields
    if (path.endsWith("/api/v1/admin/dynamic-fields") && method === "GET") {
      await json(route, 200, pageOf(dynamicFields));
      return;
    }
    if (path.endsWith("/api/v1/admin/dynamic-fields") && method === "POST") {
      const body = req.postDataJSON() as Record<string, unknown>;
      const created = {
        id: ++nextId,
        internal_field: 0,
        name: body.name,
        label: body.label,
        field_order: body.field_order,
        field_type: body.field_type,
        object_type: body.object_type,
        config: body.config ?? {},
        valid_id: body.valid_id ?? 1,
        create_time: "2026-07-19T00:00:00Z",
        change_time: "2026-07-19T00:00:00Z",
      };
      dynamicFields = [...dynamicFields, created];
      await json(route, 201, created);
      return;
    }
    const dfMatch = path.match(/\/api\/v1\/admin\/dynamic-fields\/(\d+)$/);
    if (dfMatch && method === "PATCH") {
      const id = Number(dfMatch[1]);
      const body = req.postDataJSON() as Record<string, unknown>;
      dynamicFields = dynamicFields.map((f) =>
        f.id === id ? { ...f, ...body, change_time: "2026-07-19T00:00:00Z" } : f,
      );
      await json(route, 200, dynamicFields.find((f) => f.id === id));
      return;
    }

    // Per-user 2FA reset (also reachable from the users list's ⋯ menu).
    if (/\/api\/v1\/admin\/auth-config\/\d+\/reset-2fa$/.test(path) && method === "POST") {
      await route.fulfill({ status: 204, body: "" });
      return;
    }

    // Reference data feeding the queue form's required selects.
    // follow-up-possible is a bare array; system addresses, salutations and
    // signatures are paginated like the other admin resources
    // (api.listSystemAddresses() unwraps the page's `items`).
    if (path.endsWith("/api/v1/admin/system-addresses") && method === "GET") {
      await json(
        route,
        200,
        pageOf([{ id: 1, value0: "support@example.com", value1: "Support", valid_id: 1 }]),
      );
      return;
    }
    if (path.endsWith("/api/v1/admin/follow-up-possible") && method === "GET") {
      await json(route, 200, [{ id: 1, name: "possible" }]);
      return;
    }
    if (path.endsWith("/api/v1/admin/salutations") && method === "GET") {
      await json(route, 200, pageOf([{ id: 1, name: "Standard salutation", valid_id: 1 }]));
      return;
    }
    if (path.endsWith("/api/v1/admin/signatures") && method === "GET") {
      await json(route, 200, pageOf([{ id: 1, name: "Standard signature", valid_id: 1 }]));
      return;
    }

    // Everything else under /admin (roles, states, priorities, customers,
    // templates, auto-responses, postmaster filters, acl, generic agent jobs)
    // — default to an empty list for GET so unrelated admin pages render
    // without error in these focused specs.
    if (method === "GET") {
      await json(route, 200, []);
      return;
    }

    await json(route, 404, { detail: `No mock for ${method} ${path}` });
  });
}

export { loginAsAgent };
