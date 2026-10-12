import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import type { ProcessShape } from "@/components/process-designer/canvasModel";
import { ProcessDesignerPage } from "./ProcessDesignerPage";

const getProcess = vi.fn();
const getCatalog = vi.fn();
const updateProcess = vi.fn();
const validation = vi.fn();
const revisions = vi.fn();
const deployStatus = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  useParams: () => ({ processEntityId: "Process-1" }),
  Link: ({ to, children, ...rest }: { to: string; children: ReactNode; [k: string]: unknown }) => (
    <a href={to} {...rest}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/processDesignerApi", () => ({
  processDesignerApi: {
    getProcess: (...args: unknown[]) => getProcess(...args),
    catalog: (...args: unknown[]) => getCatalog(...args),
    updateProcess: (...args: unknown[]) => updateProcess(...args),
    createEntity: vi.fn(),
    validation: (...args: unknown[]) => validation(...args),
    revisions: (...args: unknown[]) => revisions(...args),
  },
}));
vi.mock("@/lib/deployStatusApi", () => ({
  deployStatusApi: { status: (...args: unknown[]) => deployStatus(...args), deploy: vi.fn() },
}));

// The canvas needs layout APIs jsdom lacks; its own rendering is covered by the e2e/demo check.
vi.mock("@/components/process-designer/DesignerCanvas", () => ({
  DesignerCanvas: ({
    shape,
    onShapeChange,
    issues,
  }: {
    shape: ProcessShape;
    onShapeChange: (s: ProcessShape) => void;
    issues?: unknown[];
  }) => (
    <div data-testid="process-designer-canvas" data-issues={issues?.length ?? 0}>
      <button
        type="button"
        onClick={() => onShapeChange({ ...shape, layout: { ...shape.layout, "Activity-a": { left: "10", top: "20" } } })}
      >
        mock-move
      </button>
    </div>
  ),
}));

const entity = (entity_id: string, name: string, config: Record<string, unknown> = {}) => ({
  entity_type: "Activity" as const,
  entity_id,
  id: 1,
  name,
  config,
  version: "v1",
  sync_state: "synced" as const,
  changed_at: "2026-10-01T10:00:00Z",
  changed_by: 1,
});

const draft = {
  process: { ...entity("Process-1", "Bestellung"), entity_type: "Process" as const },
  state_entity_id: "S1",
  state_name: "Active",
  layout: {},
  activities: [entity("Activity-a", "Antrag erfassen"), entity("Activity-b", "Genehmigung")],
  activity_dialogs: [],
  transitions: [],
  transition_actions: [],
  missing_references: [],
  pending_changes: 0,
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <ProcessDesignerPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

function setViewport(phone: boolean) {
  window.matchMedia = ((query: string) => ({
    matches: phone,
    media: query,
    addEventListener: () => {},
    removeEventListener: () => {},
  })) as unknown as typeof window.matchMedia;
}

describe("ProcessDesignerPage", () => {
  beforeEach(async () => {
    await i18n.changeLanguage("de");
    getProcess.mockResolvedValue(draft);
    validation.mockResolvedValue([]);
    revisions.mockResolvedValue([]);
    deployStatus.mockResolvedValue({
      process: {
        pending_changes: 0,
        running_live: false,
        revision: null,
        znuny_sync: { state: "not_needed", revision_id: null, message: "", since: null, overdue: false },
        deploy_mode: "local",
      },
      acl: {
        pending_changes: 0,
        running_live: false,
        revision: null,
        znuny_sync: { state: "not_needed", revision_id: null, message: "", since: null, overdue: false },
        deploy_mode: null,
      },
      ownership_active: false,
      can_deploy_from_tiqora: true,
    });
    getCatalog.mockResolvedValue({ actions: [], condition_types: [], condition_modules: [], fields: [], interfaces: [], permissions: [], account_time: false });
  });
  afterEach(() => {
    // @ts-expect-error restore the jsdom default (no matchMedia)
    delete window.matchMedia;
    vi.clearAllMocks();
  });

  it("renders the layout grid with palette, canvas and panel on a wide screen", async () => {
    setViewport(false);
    renderPage();
    expect(await screen.findByTestId("process-designer-layout")).toBeInTheDocument();
    expect(screen.getByTestId("process-designer-palette")).toBeInTheDocument();
    expect(screen.getByTestId("process-designer-canvas")).toBeInTheDocument();
    expect(screen.getByTestId("process-designer-panel")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Bestellung" })).toBeInTheDocument();
    expect(getProcess.mock.calls[0][0]).toBe("Process-1");
  });

  it("phone width renders the read-only list", async () => {
    setViewport(true);
    renderPage();
    expect(await screen.findByTestId("process-designer-phone-list")).toBeInTheDocument();
    expect(screen.queryByTestId("process-designer-layout")).not.toBeInTheDocument();
    expect(screen.queryByTestId("process-designer-canvas")).not.toBeInTheDocument();
    expect(screen.getByTestId("process-designer-activity-Activity-a")).toHaveTextContent("Antrag erfassen");
    expect(screen.getByText(i18n.t("admin.processDesigner.phoneReadOnly"))).toBeInTheDocument();
  });

  it("shows an error when the draft cannot be loaded", async () => {
    setViewport(false);
    getProcess.mockRejectedValue(new Error("boom"));
    renderPage();
    expect(await screen.findByText(i18n.t("admin.processDesigner.loadError"))).toBeInTheDocument();
  });

  describe("editing", () => {
    beforeEach(() => setViewport(false));

    async function editOnce() {
      renderPage();
      fireEvent.click(await screen.findByText("mock-move"));
    }

    it("Speichern is disabled until the canvas is edited, then PUTs the shape with the version", async () => {
      updateProcess.mockResolvedValue({ ...draft, process: { ...draft.process, version: "v2" } });
      renderPage();
      const save = await screen.findByTestId("process-designer-save");
      expect(save).toBeDisabled();
      fireEvent.click(screen.getByText("mock-move"));
      expect(save).toBeEnabled();
      expect(screen.getByTestId("process-designer-dirty")).toBeInTheDocument();
      fireEvent.click(save);
      await waitFor(() => expect(updateProcess).toHaveBeenCalledTimes(1));
      const [eid, body] = updateProcess.mock.calls[0];
      expect(eid).toBe("Process-1");
      expect(body).toMatchObject({
        expected_version: "v1",
        name: "Bestellung",
        state_entity_id: "S1",
        path: {},
        layout: { "Activity-a": { left: "10", top: "20" } },
        start_activity: "",
        start_activity_dialog: "",
      });
      await waitFor(() => expect(screen.getByTestId("process-designer-save")).toBeDisabled());
    });

    it("changing the process state in the panel marks the draft dirty and is saved with the canvas", async () => {
      updateProcess.mockResolvedValue({ ...draft, state_entity_id: "S3", process: { ...draft.process, version: "v2" } });
      renderPage();
      const save = await screen.findByTestId("process-designer-save");
      expect(save).toBeDisabled();
      fireEvent.click(await screen.findByTestId("process-panel-state"));
      fireEvent.click(screen.getByTestId("process-panel-state-menu-option-S3"));
      expect(save).toBeEnabled();
      expect(screen.getByTestId("process-designer-dirty")).toBeInTheDocument();
      fireEvent.click(save);
      await waitFor(() => expect(updateProcess).toHaveBeenCalledTimes(1));
      expect(updateProcess.mock.calls[0][1]).toMatchObject({ state_entity_id: "S3", name: "Bestellung" });
      await waitFor(() => expect(screen.getByTestId("process-designer-save")).toBeDisabled());
    });

    it("Ctrl+S saves", async () => {
      updateProcess.mockResolvedValue(draft);
      await editOnce();
      fireEvent.keyDown(window, { key: "s", ctrlKey: true });
      await waitFor(() => expect(updateProcess).toHaveBeenCalledTimes(1));
    });

    it("409 shows the conflict banner and keeps the local edits", async () => {
      updateProcess.mockRejectedValue(new ApiError(409, "version", "/x"));
      await editOnce();
      fireEvent.click(screen.getByTestId("process-designer-save"));
      expect(await screen.findByTestId("process-designer-conflict")).toHaveTextContent(
        i18n.t("admin.processDesigner.conflict"),
      );
      expect(screen.getByTestId("process-designer-save")).toBeEnabled();
      expect(screen.getByTestId("process-designer-dirty")).toBeInTheDocument();
    });

    it("Verwerfen drops the local edits", async () => {
      await editOnce();
      fireEvent.click(screen.getByTestId("process-designer-discard"));
      expect(screen.getByTestId("process-designer-save")).toBeDisabled();
      expect(screen.queryByTestId("process-designer-dirty")).not.toBeInTheDocument();
    });

    it("registers beforeunload only while there are unsaved edits", async () => {
      const add = vi.spyOn(window, "addEventListener");
      renderPage();
      await screen.findByText("mock-move");
      expect(add.mock.calls.some(([type]) => type === "beforeunload")).toBe(false);
      fireEvent.click(screen.getByText("mock-move"));
      await waitFor(() => expect(add.mock.calls.some(([type]) => type === "beforeunload")).toBe(true));
      const handler = add.mock.calls.find(([type]) => type === "beforeunload")?.[1] as (e: Event) => void;
      const ev = new Event("beforeunload", { cancelable: true });
      handler(ev);
      expect(ev.defaultPrevented).toBe(true);
      add.mockRestore();
    });
  });

  it("designer route is lazy: the page module is not imported by router.tsx", () => {
    const src = readFileSync(resolve(__dirname, "../../../router.tsx"), "utf8");
    expect(src).not.toMatch(/^import[^;]*ProcessDesignerPage[^;]*from/m);
    expect(src).toMatch(/lazyRouteComponent\(\s*\(\) => import\("@\/routes\/admin\/process-designer\/ProcessDesignerPage"\)/);
  });

  describe("checks, versions and deploy", () => {
    beforeEach(() => setViewport(false));

    it("passes the check result to the canvas for the badges", async () => {
      validation.mockResolvedValue([
        { code: "activity_dead_end", severity: "warning", entity_type: "Activity", entity_id: "Activity-a", field: "", params: { name: "Antrag erfassen" } },
      ]);
      renderPage();
      await waitFor(() => expect(screen.getByTestId("process-designer-canvas")).toHaveAttribute("data-issues", "1"));
    });

    it("Prüfen lists the issues and a click selects the entity", async () => {
      validation.mockResolvedValue([
        { code: "activity_dead_end", severity: "warning", entity_type: "Activity", entity_id: "Activity-a", field: "", params: { name: "Antrag erfassen" } },
      ]);
      renderPage();
      fireEvent.click(await screen.findByTestId("process-designer-check"));
      fireEvent.click(await screen.findByTestId("validation-issue"));
      // the side panel now shows the selected step instead of the process panel
      expect(await screen.findByTestId("activity-panel-name")).toHaveValue("Antrag erfassen");
      expect(screen.queryByTestId("process-panel-name")).not.toBeInTheDocument();
    });

    it("Versionen opens the revision list", async () => {
      renderPage();
      fireEvent.click(await screen.findByTestId("process-designer-versions"));
      expect(await screen.findByTestId("revisions-empty")).toBeInTheDocument();
    });

    it("offers Bereitstellen from the deploy bar", async () => {
      renderPage();
      expect(await screen.findByTestId("deploy-bar-deploy")).toBeInTheDocument();
    });

    it("has no check, versions or deploy on a phone", async () => {
      setViewport(true);
      renderPage();
      await screen.findByTestId("process-designer-phone-list");
      expect(screen.queryByTestId("process-designer-check")).not.toBeInTheDocument();
      expect(screen.queryByTestId("deploy-bar")).not.toBeInTheDocument();
    });
  });
});
