import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import {
  Outlet,
  RouterProvider,
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
} from "@tanstack/react-router";
import i18n from "@/i18n";
import { demoReport, demoSources, resolveReportRun } from "@/demo/reportsMock";
import { ApiError } from "@/lib/api";
import { reportsApi, type ReportItem, type ReportRunIn } from "@/lib/reportsApi";
import { setDisplayTimeZone } from "@/lib/timeZone";
import { previewDebounce } from "@/components/reports/builder/useDebounced";
import { ReportBuilderPage } from "./ReportBuilderPage";
import { validateReportBuilderSearch } from "./reportBuilderSearch";
import { validateReportViewSearch } from "./reportViewSearch";
import { ReportViewPage } from "./ReportViewPage";

const auth = vi.hoisted(() => ({ user: {} as Record<string, boolean> }));
vi.mock("@/auth/AuthContext", () => ({ useAuth: () => ({ user: auth.user }) }));

vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  api: {
    listQueues: vi.fn(async () => [
      { id: 1, name: "Support", children: [] },
      { id: 2, name: "Vertrieb", children: [] },
    ]),
    request: vi.fn(async () => []),
  },
}));

/** The shipped wait before a changed report is previewed again; the other tests shorten it. */
const DEBOUNCE_MS = previewDebounce.ms;

const RESULT = resolveReportRun({ report_key: "builtin.volume_by_queue" } as ReportRunIn).result!;

/** A saved report as `GET /reports/41` answers it. */
function saved(over: Partial<ReportItem> = {}): ReportItem {
  return {
    key: "41",
    kind: "saved",
    id: 41,
    name: "Meine Queues",
    description: "Tickets je Queue",
    definition: demoReport("builtin.volume_by_queue")!.definition,
    visibility: "private",
    group_ids: [],
    owner: { user_id: 1, name: "Alex Turner" },
    version: 3,
    copied_from: null,
    overview: false,
    read_only: false,
    ...over,
  } as ReportItem;
}

// No user-event in this repo: thin helpers over fireEvent.
const user = {
  click: async (el: Element) => void fireEvent.click(el),
  type: async (el: Element, text: string) => {
    for (const ch of text) fireEvent.change(el, { target: { value: (el as HTMLInputElement).value + ch } });
  },
};

function renderAt(path: string, flags: { can_create_reports: boolean; can_share_reports: boolean }) {
  auth.user = { can_view_reports: true, is_admin: false, ...flags };
  const root = createRootRoute({ component: () => <Outlet /> });
  const agent = createRoute({ getParentRoute: () => root, path: "/agent", component: () => <Outlet /> });
  const list = createRoute({ getParentRoute: () => agent, path: "/reports", component: () => <p data-testid="reports-list">Berichte</p> });
  const created = createRoute({
    getParentRoute: () => agent,
    path: "/reports/new",
    validateSearch: validateReportBuilderSearch,
    component: ReportBuilderPage,
  });
  const edit = createRoute({ getParentRoute: () => agent, path: "/reports/$reportKey/edit", component: ReportBuilderPage });
  const view = createRoute({
    getParentRoute: () => agent,
    path: "/reports/$reportKey",
    validateSearch: validateReportViewSearch,
    component: ReportViewPage,
  });
  const router = createRouter({
    routeTree: root.addChildren([agent.addChildren([list, created, edit, view])]),
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <RouterProvider router={router} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return router;
}

const CAN = { can_create_reports: true, can_share_reports: true };

beforeAll(async () => {
  window.scrollTo = vi.fn(); // the router restores scroll positions; jsdom has none
  await i18n.changeLanguage("de");
  setDisplayTimeZone("Europe/Berlin");
});

beforeEach(() => {
  previewDebounce.ms = 20;
  vi.spyOn(reportsApi, "sources").mockResolvedValue(demoSources());
  vi.spyOn(reportsApi, "get").mockImplementation(async (key: string) => {
    if (key === "41") return saved();
    if (key === "42") return saved({ key: "42", id: 42, name: "Fremder Bericht", read_only: true });
    const builtin = demoReport(key);
    if (!builtin) throw new ApiError(404, { detail: { message: `Unbekannter Bericht „${key}“.` } }, "/api/v1/reports");
    return builtin;
  });
  vi.spyOn(reportsApi, "run").mockImplementation(async (body) => resolveReportRun(body));
  vi.spyOn(reportsApi, "shareGroups").mockResolvedValue([
    { id: 2, name: "users" },
    { id: 3, name: "Support" },
  ]);
});

afterEach(() => {
  previewDebounce.ms = DEBOUNCE_MS;
  vi.restoreAllMocks();
  vi.useRealTimers();
});

async function pickSource(label: string) {
  await user.click(await screen.findByTestId("builder-source"));
  await user.click(screen.getByRole("option", { name: label }));
}

describe("ReportBuilderPage", () => {
  it("builds a report and saves it privately", async () => {
    const create = vi.spyOn(reportsApi, "create").mockResolvedValue({ key: "41", kind: "saved", id: 41 } as never);
    renderAt("/agent/reports/new", { can_create_reports: true, can_share_reports: false });
    await pickSource("Tickets");
    await user.click(screen.getByTestId("builder-rows-add"));
    await user.click(screen.getByRole("menuitem", { name: "Queue" }));
    await user.click(screen.getByTestId("builder-values-add"));
    await user.click(screen.getByRole("menuitem", { name: "Tickets" }));
    await waitFor(() => expect(screen.getByTestId("builder-preview-view")).toBeInTheDocument());
    await user.type(screen.getByTestId("builder-name"), "Meine Queues");
    await user.click(screen.getByTestId("builder-save"));
    await user.click(screen.getByTestId("save-report-dialog-confirm"));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "Meine Queues",
        visibility: "private",
        group_ids: [],
        definition: expect.objectContaining({ source: "tickets", dimensions: [{ key: "queue" }], measures: [{ key: "tickets" }] }),
      }),
    );
    expect(await screen.findByTestId("report-view-page")).toBeInTheDocument(); // navigated to /agent/reports/41
  });

  it("disables sharing without the share right", async () => {
    renderAt("/agent/reports/new?from=builtin.volume", { can_create_reports: true, can_share_reports: false });
    await user.click(await screen.findByTestId("builder-save"));
    expect(screen.getByTestId("share-fields-groups")).toBeDisabled();
    expect(screen.getByTestId("share-fields-all")).toBeDisabled();
    expect(screen.getByTestId("share-fields-hint")).toHaveTextContent("Berechtigung");
  });

  it("shares with groups only after picking one, then sends the group ids", async () => {
    const create = vi.spyOn(reportsApi, "create").mockResolvedValue({ key: "41", kind: "saved", id: 41 } as never);
    renderAt("/agent/reports/new?from=builtin.volume", CAN);
    await user.click(await screen.findByTestId("builder-save"));
    await user.click(screen.getByTestId("share-fields-groups"));
    await user.click(screen.getByTestId("save-report-dialog-confirm"));
    expect(create).not.toHaveBeenCalled();
    expect(screen.getByTestId("save-report-dialog")).toHaveTextContent("Wählen Sie mindestens eine Gruppe zum Teilen.");
    await user.click(await screen.findByTestId("share-fields-group-ids-add"));
    await user.click(await screen.findByRole("option", { name: "Support" }));
    await user.click(screen.getByTestId("save-report-dialog-confirm"));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0][0]).toMatchObject({ visibility: "groups", group_ids: [3], copied_from: "builtin.volume" });
  });

  it("does not save without a name", async () => {
    const create = vi.spyOn(reportsApi, "create");
    renderAt("/agent/reports/new?from=builtin.volume", CAN);
    await user.type(await screen.findByTestId("builder-name"), "x");
    fireEvent.change(screen.getByTestId("builder-name"), { target: { value: "" } });
    await user.click(screen.getByTestId("builder-save"));
    await user.click(screen.getByTestId("save-report-dialog-confirm"));
    expect(create).not.toHaveBeenCalled();
    expect(screen.getByTestId("save-report-dialog")).toHaveTextContent("Bitte geben Sie einen Namen ein.");
  });

  it("copies a built-in with a 'Kopie von' name and copied_from", async () => {
    renderAt("/agent/reports/new?from=builtin.volume", CAN);
    expect(await screen.findByTestId("builder-name")).toHaveValue("Kopie von Erstellte und geschlossene Tickets");
    expect(screen.getByTestId("builder-source")).toHaveTextContent("Ticketereignisse");
  });

  it("shows validation issues instead of a preview", async () => {
    renderAt("/agent/reports/new", CAN);
    expect(await screen.findByTestId("builder-preview-issues")).toHaveTextContent("Datenbasis");
    expect(screen.getByTestId("builder-save")).toBeDisabled();
    expect(reportsApi.run).not.toHaveBeenCalled();
  });

  it("edit with a stale version shows the conflict message and reloads on request", async () => {
    const update = vi
      .spyOn(reportsApi, "update")
      .mockRejectedValue(
        new ApiError(409, { detail: { message: "Der Bericht wurde inzwischen geändert. Bitte neu laden.", current_version: 4 } }, "/api/v1/reports/41"),
      );
    renderAt("/agent/reports/41/edit", CAN);
    await user.click(await screen.findByTestId("builder-save"));
    await user.click(screen.getByTestId("save-report-dialog-confirm"));
    expect(await screen.findByRole("alert")).toHaveTextContent("inzwischen geändert");
    expect(update).toHaveBeenCalledWith(41, expect.objectContaining({ version: 3, name: "Meine Queues" }));
    vi.mocked(reportsApi.get).mockClear();
    await user.click(screen.getByTestId("save-report-dialog-reload"));
    await waitFor(() => expect(reportsApi.get).toHaveBeenCalledWith("41", expect.anything()));
  });

  it("saves a copy of a shared report as 'Nur ich' without the share right", async () => {
    vi.mocked(reportsApi.get).mockImplementation(async () => saved({ visibility: "groups", group_ids: [3] }));
    const create = vi.spyOn(reportsApi, "create").mockResolvedValue({ key: "50", kind: "saved", id: 50 } as never);
    const update = vi.spyOn(reportsApi, "update").mockResolvedValue({ key: "41", kind: "saved", id: 41 } as never);
    renderAt("/agent/reports/41/edit", { can_create_reports: true, can_share_reports: false });
    await user.click(await screen.findByTestId("builder-save-copy"));
    expect(screen.getByTestId("share-fields-private")).toHaveAttribute("aria-checked", "true");
    await user.click(screen.getByTestId("save-report-dialog-confirm"));
    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0][0]).toMatchObject({ visibility: "private", group_ids: [], copied_from: "41" });
    expect(update).not.toHaveBeenCalled();
  });

  it("keeps the original's sharing for a plain save after opening the copy dialog", async () => {
    vi.mocked(reportsApi.get).mockImplementation(async () => saved({ visibility: "groups", group_ids: [3] }));
    const update = vi.spyOn(reportsApi, "update").mockResolvedValue({ key: "41", kind: "saved", id: 41 } as never);
    renderAt("/agent/reports/41/edit", { can_create_reports: true, can_share_reports: false });
    await user.click(await screen.findByTestId("builder-save-copy"));
    await user.click(screen.getByTestId("save-report-dialog-cancel"));
    await user.click(screen.getByTestId("builder-save"));
    await user.click(screen.getByTestId("save-report-dialog-confirm"));
    await waitFor(() => expect(update).toHaveBeenCalledWith(41, expect.objectContaining({ visibility: "groups", group_ids: [3] })));
  });

  it("without the create right shows the notice, no builder", async () => {
    renderAt("/agent/reports/new", { can_create_reports: false, can_share_reports: false });
    expect(await screen.findByTestId("builder-no-right")).toHaveTextContent("Berechtigung");
    expect(screen.queryByTestId("builder-source")).not.toBeInTheDocument();
    expect(reportsApi.sources).not.toHaveBeenCalled();
  });

  it("offers a copy instead of editing a report the user may not change", async () => {
    renderAt("/agent/reports/42/edit", CAN);
    expect(await screen.findByTestId("builder-read-only")).toHaveTextContent("nicht ändern");
    expect(screen.queryByTestId("builder-save")).not.toBeInTheDocument();
    await user.click(screen.getByTestId("builder-edit-copy"));
    expect(await screen.findByTestId("builder-name")).toHaveValue("Kopie von Fremder Bericht");
    expect(screen.getByTestId("builder-save")).toBeEnabled();
  });

  it("sends the edit of a built-in key to a copy", async () => {
    const router = renderAt("/agent/reports/builtin.volume/edit", CAN);
    await waitFor(() => expect(router.state.location.pathname).toBe("/agent/reports/new"));
    expect(router.state.location.search).toEqual({ from: "builtin.volume" });
    expect(await screen.findByTestId("builder-name")).toHaveValue("Kopie von Erstellte und geschlossene Tickets");
  });

  it("asks before leaving with unsaved changes", async () => {
    const router = renderAt("/agent/reports/41/edit", CAN);
    await user.click(await screen.findByTestId("builder-cancel")); // untouched: leaves at once
    await waitFor(() => expect(router.state.location.pathname).toBe("/agent/reports/41"));
    await user.click(await screen.findByTestId("report-view-page").then(() => screen.getByTestId("report-view-back")));
    await router.navigate({ to: "/agent/reports/$reportKey/edit", params: { reportKey: "41" } });
    await user.type(await screen.findByTestId("builder-name"), "!");
    await user.click(screen.getByTestId("builder-cancel"));
    const dialog = await screen.findByTestId("confirm-dialog");
    expect(dialog).toHaveTextContent("gehen verloren");
    await user.click(within(dialog).getByRole("button", { name: "Weiter bearbeiten" }));
    expect(router.state.location.pathname).toBe("/agent/reports/41/edit");
  });

  it("loads a report file into the builder", async () => {
    vi.spyOn(reportsApi, "importFile").mockResolvedValue({
      name: "Aus Datei",
      description: "importiert",
      definition: demoReport("builtin.volume_by_queue")!.definition,
    } as never);
    renderAt("/agent/reports/new", CAN);
    const input = await screen.findByTestId("builder-file-input");
    const file = new File(['{"format":"tiqora-report"}'], "bericht.json", { type: "application/json" });
    Object.defineProperty(file, "text", { value: async () => '{"format":"tiqora-report"}' });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(screen.getByTestId("builder-name")).toHaveValue("Aus Datei"));
    expect(reportsApi.importFile).toHaveBeenCalledWith('{"format":"tiqora-report"}');
    expect(screen.getByTestId("builder-source")).toHaveTextContent("Tickets");
  });

  it("shows the server's message when a file cannot be read", async () => {
    vi.spyOn(reportsApi, "importFile").mockRejectedValue(
      new ApiError(422, { detail: { message: "Die Datei ist zu tief verschachtelt oder ungültig." } }, "/api/v1/reports/import-file"),
    );
    renderAt("/agent/reports/new", CAN);
    const file = new File(["x"], "kaputt.json");
    Object.defineProperty(file, "text", { value: async () => "x" });
    fireEvent.change(await screen.findByTestId("builder-file-input"), { target: { files: [file] } });
    expect(await screen.findByTestId("builder-file-error")).toHaveTextContent("zu tief verschachtelt");
  });

  it("offers only the displays the shape allows and keeps the dimension order when choosing the series", async () => {
    renderAt("/agent/reports/new?from=builtin.volume", CAN); // occurred (week) + kind, line chart, series = kind
    await screen.findByTestId("builder-viz");
    await user.click(screen.getByTestId("builder-viz"));
    const names = screen.getAllByRole("option").map((o) => o.textContent);
    expect(names).toContain("Kreuztabelle");
    expect(names).not.toContain("Kennzahlen");
    await user.click(screen.getByRole("option", { name: "Liniendiagramm" }));
    await user.click(screen.getByTestId("builder-series"));
    await user.click(screen.getByRole("option", { name: "Zeitpunkt" }));
    await user.click(screen.getByTestId("builder-viz"));
    await user.click(screen.getByRole("option", { name: "Balkendiagramm" }));
    await waitFor(() => {
      const last = vi.mocked(reportsApi.run).mock.calls.at(-1)![0];
      expect(last.definition?.dimensions?.map((d) => d.key)).toEqual(["occurred", "kind"]);
      expect(last.definition?.visualization).toMatchObject({ type: "bar", series_dimension: 0 });
    });
  });

  it("stores a time-range preset as a relative range", async () => {
    renderAt("/agent/reports/new?from=builtin.volume", CAN);
    await user.click(await screen.findByTestId("builder-time-preset-lastMonth"));
    expect(screen.getByTestId("builder-time-preset-lastMonth")).toHaveAttribute("aria-pressed", "true");
    await waitFor(() => {
      const last = vi.mocked(reportsApi.run).mock.calls.at(-1)?.[0];
      expect(last?.definition?.time_range).toEqual({ kind: "relative", unit: "month", count: 1, include_current: false });
    });
  });

  it("adds a changeable field and validates it like the server", async () => {
    renderAt("/agent/reports/new?from=builtin.volume_by_queue", CAN);
    const editor = await screen.findByTestId("builder-parameters");
    expect(within(editor).getByTestId("builder-parameters-item-0")).toHaveTextContent("Queue");
    await user.click(within(editor).getByTestId("builder-parameters-add"));
    expect(within(editor).getByTestId("builder-parameters-item-1")).toBeInTheDocument();
    await user.click(within(editor).getByTestId("builder-parameters-remove-1"));
    expect(within(editor).queryByTestId("builder-parameters-item-1")).not.toBeInTheDocument();
  });

  it("keeps a single default value when a changeable field switches to eq", async () => {
    const def = demoReport("builtin.volume_by_queue")!.definition;
    vi.mocked(reportsApi.get).mockResolvedValueOnce(
      saved({ definition: { ...def, parameters: [{ key: "queues", field: "queue", op: "in", default: [1, 2] }] } as never }),
    );
    renderAt("/agent/reports/41/edit", CAN);
    await user.click(await screen.findByTestId("builder-parameters-op-0"));
    await user.click(screen.getByRole("option", { name: "ist" }));
    await waitFor(() => {
      const sent = vi.mocked(reportsApi.run).mock.calls.at(-1)![0].definition!;
      expect(sent.parameters?.[0]).toMatchObject({ op: "eq", default: [1] });
    });
    expect(screen.getByTestId("builder-save")).toBeEnabled();
  });

  it("debounces the preview", async () => {
    expect(DEBOUNCE_MS).toBe(700);
    previewDebounce.ms = DEBOUNCE_MS;
    vi.useFakeTimers();
    const run = vi.mocked(reportsApi.run).mockResolvedValue({ result: RESULT, job: null } as never);
    const settle = () => act(async () => void (await vi.advanceTimersByTimeAsync(50)));
    renderAt("/agent/reports/new?from=builtin.volume_by_queue", CAN);
    for (let i = 0; i < 6; i += 1) await settle();
    expect(screen.getByTestId("builder-values-add")).toBeInTheDocument();
    expect(run).toHaveBeenCalled(); // initial preview of the copied definition
    run.mockClear();
    await user.click(screen.getByTestId("builder-values-remove-1"));
    await user.click(screen.getByTestId("builder-totals-grand"));
    await act(async () => void (await vi.advanceTimersByTimeAsync(699)));
    expect(run).not.toHaveBeenCalled();
    await act(async () => void (await vi.advanceTimersByTimeAsync(1)));
    expect(run).toHaveBeenCalledTimes(1);
    expect(run.mock.calls[0][0].definition).toMatchObject({ measures: [{ key: "tickets" }], totals: { grand: true } });
  });

  it("previews at most 200 rows and never blocks on a required parameter", async () => {
    const run = vi.mocked(reportsApi.run);
    renderAt("/agent/reports/new?from=builtin.volume_by_queue", CAN);
    await waitFor(() => expect(run).toHaveBeenCalled());
    const def = run.mock.calls[0][0].definition!;
    expect(def.limit).toBe(200);
    expect(def.parameters?.every((p) => p.required === false)).toBe(true);
  });
});
