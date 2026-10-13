import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { api, ApiError } from "@/lib/api";
import type { ItsmAdmin, PriorityMatrix, SetupPlan } from "@/lib/itsmApi";
import { ItsmPage } from "./ItsmPage";

const admin = (over: Partial<ItsmAdmin> = {}): ItsmAdmin => ({
  mode: "znuny_readthrough",
  source: "auto",
  stored: false,
  changed_at: null,
  changed_by: null,
  import_run_id: null,
  previous_mode: null,
  switched: false,
  switch: null,
  detected: {
    packages: { ITSMCore: "6.5.1" },
    core_installed: true,
    cmdb_installed: true,
    change_installed: false,
    profile: "znuny-6.5-itsm",
    unsupported_reason: null,
  },
  cmdb_empty: true,
  allowed_transitions: ["off"],
  ...over,
});

const plan: SetupPlan = {
  complete: false,
  items: [
    { group: "incident_problem", kind: "ticket_type", name: "Incident", status: "present", detail: null },
    { group: "incident_problem", kind: "ticket_state", name: "Workaround", status: "missing", detail: null },
    { group: "change", kind: "ticket_type", name: "Change", status: "missing", detail: null },
  ],
};

const matrix = (editable: boolean): PriorityMatrix => ({
  source: editable ? "tiqora" : "znuny",
  editable,
  enabled: true,
  criticality_values: ["low"],
  impact_values: ["small"],
  cells: [{ criticality: "low", impact: "small", priority_id: 3 }],
});

let current: ItsmAdmin;
let currentMatrix: PriorityMatrix;
let request: ReturnType<typeof vi.spyOn>;

/** The GET answers; tests wrap it to answer one write. */
async function reads(method: string, path: string): Promise<unknown> {
  if (path === "/api/v1/admin/itsm" && method === "GET") return current;
  if (path === "/api/v1/admin/itsm/setup" && method === "GET") return plan;
  if (path === "/api/v1/admin/itsm/priority-matrix" && method === "GET") return currentMatrix;
  if (path === "/api/v1/admin/priorities" && method === "GET") {
    return { items: [{ id: 3, name: "3 normal" }], total: 1, page: 1, page_size: 500 };
  }
  throw new Error(`unexpected ${method} ${path}`);
}

beforeEach(() => {
  current = admin();
  currentMatrix = matrix(false);
  request = vi.spyOn(api, "request").mockImplementation(reads as never);
});
afterEach(() => vi.restoreAllMocks());

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <ItsmPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("ItsmPage", () => {
  it("shows mode, source, detected packages and only the allowed transitions", async () => {
    renderPage();
    expect(await screen.findByTestId("itsm-mode-current")).toBeInTheDocument();
    expect(screen.getByTestId("itsm-mode-source").textContent).toBe(i18n.t("admin.itsm.mode.sources.auto"));
    expect(screen.getByTestId("itsm-detected")).toHaveTextContent("ITSMCore 6.5.1");
    expect(screen.getByTestId("itsm-switch-off")).toBeInTheDocument();
    expect(screen.queryByTestId("itsm-switch-tiqora")).toBeNull();
    expect(screen.queryByTestId("itsm-switch-znuny_readthrough")).toBeNull();
    // Mode A: the explanation names the importer; the link stays hidden while its route does not exist
    expect(screen.getByTestId("itsm-mode-a-note")).toHaveTextContent(i18n.t("admin.itsm.mode.importHint"));
    expect(screen.queryByTestId("itsm-import-link")).toBeNull();
  });

  it("states how the mode was set for admin and importer", async () => {
    current = admin({ source: "importer", mode: "tiqora", allowed_transitions: ["off"] });
    renderPage();
    expect((await screen.findByTestId("itsm-mode-source")).textContent).toBe(i18n.t("admin.itsm.mode.sources.importer"));
    expect(screen.queryByTestId("itsm-mode-a-note")).toBeNull();
  });

  it("puts the PUT /mode body on the wire for a transition", async () => {
    renderPage();
    request.mockImplementation((async (method: string, path: string, opts?: { body?: unknown }) => {
      if (method === "PUT") {
        expect(path).toBe("/api/v1/admin/itsm/mode");
        expect(opts?.body).toEqual({ mode: "off" });
        current = admin({ mode: "off", source: "admin", allowed_transitions: ["znuny_readthrough"] });
        return current;
      }
      return reads(method, path);
    }) as never);
    fireEvent.click(await screen.findByTestId("itsm-switch-off"));
    await waitFor(() => expect(screen.getByTestId("itsm-switch-znuny_readthrough")).toBeInTheDocument());
    expect(screen.getByTestId("itsm-mode-source").textContent).toBe(i18n.t("admin.itsm.mode.sources.admin"));
  });

  it("shows the reason of a refused transition (409)", async () => {
    renderPage();
    request.mockImplementation((async (method: string, path: string) => {
      if (method === "PUT") {
        throw new ApiError(
          409,
          { detail: { code: "mode_transition_not_allowed", from: "znuny_readthrough", to: "off", reason: "tiqora_cmdb_not_empty" } },
          "/api/v1/admin/itsm/mode",
        );
      }
      return reads(method, path);
    }) as never);
    fireEvent.click(await screen.findByTestId("itsm-switch-off"));
    const alert = await screen.findByTestId("itsm-mode-error");
    expect(alert).toHaveTextContent(i18n.t("admin.itsm.mode.reasons.tiqora_cmdb_not_empty"));
  });

  it("applies the setup for the checked groups", async () => {
    renderPage();
    expect(await screen.findByTestId("itsm-setup-status-ticket_state-Workaround")).toHaveTextContent(
      i18n.t("admin.itsm.setup.status.missing"),
    );
    fireEvent.click(screen.getByTestId("itsm-setup-check-change"));
    request.mockImplementation((async (method: string, path: string, opts?: { body?: unknown }) => {
      if (method === "POST" && path === "/api/v1/admin/itsm/setup") {
        expect(opts?.body).toEqual({ groups: ["incident_problem", "change"] });
        return { complete: true, items: plan.items.map((i) => ({ ...i, status: "present" })) };
      }
      return reads(method, path);
    }) as never);
    fireEvent.click(screen.getByTestId("itsm-setup-apply"));
    await waitFor(() =>
      expect(screen.getByTestId("itsm-setup-status-ticket_state-Workaround")).toHaveTextContent(
        i18n.t("admin.itsm.setup.status.present"),
      ),
    );
  });

  it("shows the Znuny matrix read-only in Mode A and an editable one in Mode B", async () => {
    renderPage();
    expect(await screen.findByTestId("priority-matrix-readonly")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("matrix-cell-0-0")).toHaveTextContent("3 normal"));
    expect(screen.queryByTestId("priority-matrix-save")).toBeNull();
  });

  it("makes the matrix editable when Tiqora leads", async () => {
    current = admin({ mode: "tiqora", allowed_transitions: ["off"] });
    currentMatrix = matrix(true);
    renderPage();
    expect(await screen.findByTestId("priority-matrix-save")).toBeInTheDocument();
    expect(screen.queryByTestId("priority-matrix-readonly")).toBeNull();
  });

  it("hides setup and matrix while the ITSM is off", async () => {
    current = admin({ mode: "off", allowed_transitions: ["znuny_readthrough"] });
    renderPage();
    expect(await screen.findByTestId("itsm-switch-znuny_readthrough")).toBeInTheDocument();
    expect(screen.queryByTestId("itsm-setup-card")).toBeNull();
    expect(screen.queryByTestId("itsm-matrix-card")).toBeNull();
  });
});
