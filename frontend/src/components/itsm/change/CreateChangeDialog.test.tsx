import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { I18nextProvider } from "react-i18next";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import type { ChangeTemplateSummary } from "@/lib/changeApi";
import type { Job } from "@/lib/jobsApi";
import { makeCi } from "../testSupport";
import { CreateChangeDialog } from "./CreateChangeDialog";

const { templates, create, listQueues, listReferenceServices, getJob } = vi.hoisted(() => ({
  templates: vi.fn(),
  create: vi.fn(),
  listQueues: vi.fn(),
  listReferenceServices: vi.fn(),
  getJob: vi.fn(),
}));
vi.mock("@/lib/changeApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/changeApi")>("@/lib/changeApi");
  return { ...actual, changeApi: { ...actual.changeApi, templates, create } };
});
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, api: { ...actual.api, listQueues, listReferenceServices } };
});
vi.mock("@/lib/jobsApi", async () => {
  const actual = await vi.importActual<typeof import("@/lib/jobsApi")>("@/lib/jobsApi");
  return { ...actual, jobsApi: { ...actual.jobsApi, get: getJob } };
});
// The picker has its own tests; here it only hands back one CI.
vi.mock("@/components/itsm/CiPicker", () => ({
  CiPicker: ({ onPick }: { onPick: (ci: ReturnType<typeof makeCi>, linkType: string | null) => void }) => (
    <button type="button" data-testid="ci-picker-stub" onClick={() => onPick(makeCi(5), null)}>
      pick
    </button>
  ),
}));

const template = (id: number, name: string, over: Partial<ChangeTemplateSummary> = {}): ChangeTemplateSummary => ({
  id,
  name,
  description: null,
  change_type: "normal",
  is_standard: false,
  defaults: { portal_visible: false },
  task_count: 0,
  ...over,
});

const job = (over: Partial<Job> = {}): Job =>
  ({
    id: 77,
    kind: "core.itsm_change_tasks",
    status: "succeeded",
    title_key: "jobs.kinds.core.itsm_change_tasks",
    progress_done: 2,
    progress_total: 2,
    progress_message: null,
    cancel_requested: false,
    error: null,
    result: { created: [1, 2], failed: [] },
    files: [],
    created_by: 1,
    created_at: "2026-10-01T00:00:00Z",
    started_at: null,
    finished_at: null,
    ...over,
  }) as Job;

const onClose = vi.fn();
const onCreated = vi.fn();

function renderDialog() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <CreateChangeDialog onClose={onClose} onCreated={onCreated} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const created = (over: Record<string, unknown> = {}) => ({ change: { ticket_id: 140, tn: "20261012140" }, tasks_job_id: null, warnings: [], ...over });

beforeEach(async () => {
  await i18n.changeLanguage("de");
  onClose.mockReset();
  onCreated.mockReset();
  templates.mockReset().mockResolvedValue([
    template(1, "Server-Wartung"),
    template(2, "Notfall-Vorlage", { change_type: "emergency" }),
    template(3, "Neuer Benutzer", { change_type: "standard", is_standard: true, defaults: { portal_visible: false, title: "Benutzer anlegen", queue_id: 4, service_ids: [2] } }),
  ]);
  create.mockReset().mockResolvedValue(created());
  listQueues.mockReset().mockResolvedValue([
    { id: 4, name: "Support::Netz", valid: true, group_id: 1 },
    { id: 9, name: "Alt", valid: false, group_id: 1 },
  ]);
  listReferenceServices.mockReset().mockResolvedValue([
    { id: 1, name: "E-Mail" },
    { id: 2, name: "VPN" },
  ]);
  getJob.mockReset().mockResolvedValue(job());
});
afterEach(cleanup);

const fillTitle = (v: string) => fireEvent.change(screen.getByTestId("change-create-title"), { target: { value: v } });

describe("CreateChangeDialog", () => {
  it("offers only the templates of the chosen type, standard ones only for a standard change", async () => {
    renderDialog();
    fireEvent.click(await screen.findByTestId("change-create-template"));
    expect(await screen.findByTestId("change-create-template-menu-option-1")).toBeInTheDocument();
    expect(screen.queryByTestId("change-create-template-menu-option-2")).toBeNull();
    expect(screen.queryByTestId("change-create-template-menu-option-3")).toBeNull();
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });

    fireEvent.click(screen.getByTestId("change-create-type-standard"));
    fireEvent.click(screen.getByTestId("change-create-template"));
    expect(await screen.findByTestId("change-create-template-menu-option-3")).toBeInTheDocument();
    expect(screen.queryByTestId("change-create-template-menu-option-1")).toBeNull();
    // no "without template" entry: a standard change is its template
    expect(screen.queryByTestId("change-create-template-menu-option-")).toBeNull();
  });

  it("keeps submit disabled for a standard change until a template is chosen", async () => {
    renderDialog();
    await screen.findByTestId("change-create-form");
    fillTitle("Konto anlegen");
    expect(screen.getByTestId("change-create-submit")).toBeEnabled();
    fireEvent.click(screen.getByTestId("change-create-type-standard"));
    expect(screen.getByTestId("change-create-submit")).toBeDisabled();
    fireEvent.click(screen.getByTestId("change-create-template"));
    fireEvent.click(await screen.findByTestId("change-create-template-menu-option-3"));
    expect(screen.getByTestId("change-create-submit")).toBeEnabled();
  });

  it("prefills empty fields from the template and sends the full payload", async () => {
    renderDialog();
    fireEvent.click(await screen.findByTestId("change-create-type-standard"));
    fireEvent.click(screen.getByTestId("change-create-template"));
    fireEvent.click(await screen.findByTestId("change-create-template-menu-option-3"));
    expect(screen.getByTestId("change-create-title")).toHaveValue("Benutzer anlegen");
    expect(screen.getByTestId("change-create-services")).toHaveTextContent("VPN");

    fireEvent.click(screen.getByTestId("change-create-add-ci"));
    fireEvent.click(await screen.findByTestId("ci-picker-stub"));
    expect(screen.getByTestId("change-create-cis")).toHaveTextContent("srv-5.example.org");

    fireEvent.change(screen.getByTestId("change-create-start"), { target: { value: "2026-10-24T20:00" } });
    fireEvent.change(screen.getByTestId("change-create-end"), { target: { value: "2026-10-24T22:30" } });
    fireEvent.change(screen.getByTestId("change-create-body"), { target: { value: "Konto für neue Kollegin" } });
    fireEvent.click(screen.getByTestId("change-create-submit"));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0][0]).toEqual({
      change_type: "standard",
      title: "Benutzer anlegen",
      template_id: 3,
      queue_id: 4,
      service_id: 2,
      service_ids: [],
      ci_refs: ["znuny:5"],
      planned_start: "2026-10-24T20:00:00Z",
      planned_end: "2026-10-24T22:30:00Z",
      reason: null,
      body: "Konto für neue Kollegin",
    });
  });

  it("says so when the templates cannot be loaded", async () => {
    templates.mockRejectedValue(new ApiError(409, { detail: { code: "change_mode_a" } }, "x"));
    renderDialog();
    expect(await screen.findByTestId("change-create-templates-error")).toHaveTextContent("im Modus A nicht verfügbar");
  });

  it("refuses a window that ends before it starts", async () => {
    renderDialog();
    await screen.findByTestId("change-create-form");
    fillTitle("Wartung");
    fireEvent.change(screen.getByTestId("change-create-start"), { target: { value: "2026-10-24T22:00" } });
    fireEvent.change(screen.getByTestId("change-create-end"), { target: { value: "2026-10-24T20:00" } });
    expect(screen.getByTestId("change-create-window-hint")).toHaveTextContent("Das Ende muss nach dem Beginn liegen.");
    expect(screen.getByTestId("change-create-submit")).toBeDisabled();
  });

  it("opens the change at once when there is no task job", async () => {
    renderDialog();
    await screen.findByTestId("change-create-form");
    fillTitle("Wartung");
    fireEvent.click(screen.getByTestId("change-create-submit"));
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(140));
    expect(onCreated).toHaveBeenCalledTimes(1);
  });

  it("follows the task job, then opens the change", async () => {
    create.mockResolvedValue(created({ tasks_job_id: 77 }));
    getJob.mockResolvedValue(job({ status: "running", progress_done: 1 }));
    renderDialog();
    await screen.findByTestId("change-create-form");
    fillTitle("Wartung");
    fireEvent.click(screen.getByTestId("change-create-submit"));
    expect(await screen.findByTestId("change-create-job")).toBeInTheDocument();
    expect(onCreated).not.toHaveBeenCalled();
    getJob.mockResolvedValue(job());
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(140), { timeout: 4000 });
  });

  it("stays on the result when tasks failed or the create warned", async () => {
    create.mockResolvedValue(created({ tasks_job_id: 77, warnings: ["ci_refs_truncated"] }));
    getJob.mockResolvedValue(job({ result: { created: [1], failed: [{ key: "a", code: "forbidden" }] } }));
    renderDialog();
    await screen.findByTestId("change-create-form");
    fillTitle("Wartung");
    fireEvent.click(screen.getByTestId("change-create-submit"));
    expect(await screen.findByTestId("change-create-tasks-failed")).toHaveTextContent("1 Aufgabe konnte nicht angelegt werden.");
    expect(screen.getByTestId("change-create-warnings")).toHaveTextContent("Obergrenze");
    expect(onCreated).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("change-create-open"));
    expect(onCreated).toHaveBeenCalledWith(140);
  });

  it.each([
    ["change_mode_a", 409, "im Modus A nicht verfügbar"],
    ["change_setup_missing", 409, "Die Einrichtung ist unvollständig"],
    ["standard_needs_template", 422, "braucht eine Standard-Vorlage"],
    ["queue_required", 422, "keine Standard-Queue"],
    ["forbidden", 403, "keine Changes anlegen"],
    ["some_new_code", 500, "Die Aktion ist fehlgeschlagen."],
  ])("shows the text for %s", async (code, status, text) => {
    create.mockRejectedValue(new ApiError(status, { detail: { code } }, "x"));
    renderDialog();
    await screen.findByTestId("change-create-form");
    fillTitle("Wartung");
    fireEvent.click(screen.getByTestId("change-create-submit"));
    const error = await screen.findByTestId("change-create-error");
    expect(error).toHaveTextContent(text);
    expect(error).toHaveAttribute("data-code", code);
    expect(onCreated).not.toHaveBeenCalled();
  });
});
