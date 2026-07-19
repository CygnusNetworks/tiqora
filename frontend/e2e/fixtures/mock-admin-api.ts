import type { Page, Route } from "@playwright/test";
import { mockApi, loginAsAgent } from "./mock-api";

async function json(route: Route, status: number, body: unknown) {
  await route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
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
 * dynamic fields (in-memory CRUD), used by the RequireAdmin capability probe
 * (adminGroups.list) and the admin resource pages.
 */
export async function mockAdminApi(page: Page) {
  resetAdminState();
  await mockApi(page);

  await page.route("**/api/v1/admin/**", async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();

    // Users
    if (path.endsWith("/api/v1/admin/users") && method === "GET") {
      await json(route, 200, users);
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

    // Groups (also used by RequireAdmin's capability probe)
    if (path.endsWith("/api/v1/admin/groups") && method === "GET") {
      await json(route, 200, groups);
      return;
    }

    // Queues
    if (path.endsWith("/api/v1/admin/queues") && method === "GET") {
      await json(route, 200, queues);
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
      await json(route, 200, dynamicFields);
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

    // Everything else under /admin (roles, states, priorities, customers,
    // templates, salutations, signatures, auto-responses, postmaster
    // filters, acl, generic agent jobs) — default to an empty list for GET
    // so unrelated admin pages render without error in these focused specs.
    if (method === "GET") {
      await json(route, 200, []);
      return;
    }

    await json(route, 404, { detail: `No mock for ${method} ${path}` });
  });
}

export { loginAsAgent };
