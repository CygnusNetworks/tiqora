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

function resetAdminState() {
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

    // Deployed-revision banner of the ProcessManagement / ACL admin pages
    if (path.endsWith("/api/v1/admin/deploy-status") && method === "GET") {
      const idle = { pending_changes: 0, revision: null, running_live: true };
      const status: DeployStatus = { acl: idle, process: idle, can_deploy_from_tiqora: false, ownership_active: false };
      await json(route, 200, status);
      return;
    }

    // Process designer: one draft process with two steps (ProcessDraftOut) and the catalogue.
    if (path.includes("/api/v1/admin/process-designer/") && method === "GET") {
      const ent = (entity_type: string, entity_id: string, id: number, name: string, config: Record<string, unknown>) => ({
        entity_type, entity_id, id, name, config, version: `v-${entity_id}`, sync_state: "synced",
        changed_at: "2026-10-09T09:00:00Z", changed_by: 1,
      });
      if (path.endsWith("/catalog")) {
        await json(route, 200, { actions: [], condition_types: [], condition_modules: [], fields: [], interfaces: ["AgentInterface"], permissions: [""], account_time: false });
        return;
      }
      if (path.endsWith("/process-designer/processes/Process-1")) {
        await json(route, 200, {
          process: ent("Process", "Process-1", 1, "Onboarding", {
            Description: "",
            StartActivity: "Activity-a",
            StartActivityDialog: "ActivityDialog-1",
            Path: { "Activity-a": { "Transition-1": { ActivityEntityID: "Activity-b" } }, "Activity-b": {} },
          }),
          state_entity_id: "S1",
          state_name: "Active",
          layout: { "Activity-a": { left: "40", top: "40" }, "Activity-b": { left: "300", top: "40" } },
          activities: [ent("Activity", "Activity-a", 1, "Collect info", {}), ent("Activity", "Activity-b", 2, "Done", {})],
          activity_dialogs: [],
          transitions: [ent("Transition", "Transition-1", 1, "Go on", {})],
          transition_actions: [],
          missing_references: [],
          pending_changes: 0,
        });
        return;
      }
      await json(route, 200, []);
      return;
    }

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
