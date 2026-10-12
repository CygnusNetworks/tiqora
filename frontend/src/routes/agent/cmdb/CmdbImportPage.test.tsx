import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import i18n from "@/i18n";
import { createDemoCmdb, demoCmdbJob, demoCmdbReport, resolveCmdb, type DemoCmdb } from "@/demo/cmdbMock";
import { DEMO_ITSM_MODE_KEY } from "@/demo/itsmMode";
import { api } from "@/lib/api";
import { CmdbImportPage } from "./CmdbImportPage";

let store: DemoCmdb;
/** Job ids that were started as the real import and that the test lets fail on the server side. */
let failRealJobs = false;
const realJobIds = new Set<number>();
const requests: Array<{ method: string; path: string; body: unknown }> = [];

function serveDemo() {
  vi.spyOn(api, "request").mockImplementation(async (method: string, path: string, opts?: { body?: unknown; query?: Record<string, unknown> }) => {
    requests.push({ method, path, body: opts?.body });
    const query = new URLSearchParams();
    for (const [k, v] of Object.entries(opts?.query ?? {})) {
      if (v === undefined || v === null) continue;
      query.append(k, String(v));
    }
    const job = /\/api\/v1\/jobs\/(\d+)$/.exec(path);
    if (job) {
      const j = demoCmdbJob(store, Number(job[1]));
      if (j && failRealJobs && realJobIds.has(j.id)) return { ...j, status: "failed", error: "Boom: Datenbank nicht erreichbar", result: null } as never;
      if (j) return j as never;
    }
    const out = resolveCmdb(store, method, path, opts?.body, query);
    if (!out) throw new Error(`unmocked ${method} ${path}`);
    if (method === "POST" && path.endsWith("/import-jobs") && opts?.body instanceof FormData) {
      const req = JSON.parse(String(opts.body.get("request"))) as { dry_run?: boolean };
      if (!req.dry_run) realJobIds.add((out.body as { job_id: number }).job_id);
    }
    return out.body as never;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string) => {
      const m = /\/jobs\/(\d+)\/files\/\d+/.exec(String(url));
      const text = m ? demoCmdbReport(store, Number(m[1])) : undefined;
      return text === undefined ? new Response("", { status: 404 }) : new Response(text, { status: 200 });
    }),
  );
}

async function renderPage(initial = "/agent/cmdb/import") {
  const rootRoute = createRootRoute();
  const importRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/agent/cmdb/import",
    validateSearch: (s: Record<string, unknown>): { class?: number } => (Number.isInteger(Number(s.class)) && Number(s.class) > 0 ? { class: Number(s.class) } : {}),
    component: CmdbImportPage,
  });
  const listRoute = createRoute({ getParentRoute: () => rootRoute, path: "/agent/cmdb", component: () => <div data-testid="list-stub" /> });
  const router = createRouter({ routeTree: rootRoute.addChildren([importRoute, listRoute]), history: createMemoryHistory({ initialEntries: [initial] }) });
  await router.load();
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <I18nextProvider i18n={i18n}>
        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
        <RouterProvider router={router as any} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return router;
}

const EXPORT_CSV = ["number;name;class;deployment_state;incident_state;attr:hostname;attr:ip", "SRV-0001;web-01;Server;production;operational;web-01.example.org;10.0.0.1", "SRV-0002;web-02;Server;production;operational;web-02.example.org;10.0.0.2"].join("\n");

function csvFile(text: string, name = "export.csv") {
  return new File([text], name, { type: "text/csv" });
}

async function pickClassAndFile(file: File, classId = 1) {
  fireEvent.click(await screen.findByTestId("import-class"));
  fireEvent.click(await screen.findByTestId(`import-class-menu-option-${classId}`));
  fireEvent.change(screen.getByTestId("import-file"), { target: { files: [file] } });
  await waitFor(() => expect(screen.getByTestId("import-next-1")).toBeEnabled());
}

const importRequests = () =>
  requests
    .filter((r) => r.method === "POST" && r.path.endsWith("/import-jobs"))
    .map((r) => {
      const form = r.body as FormData;
      return { file: form.get("file") as File, request: JSON.parse(String(form.get("request"))) as Record<string, unknown> };
    });

beforeEach(async () => {
  store = createDemoCmdb();
  failRealJobs = false;
  realJobIds.clear();
  requests.length = 0;
  await i18n.changeLanguage("de");
  serveDemo();
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.removeItem(DEMO_ITSM_MODE_KEY);
});

describe("CmdbImportPage", () => {
  it("shows only the notice in Mode A", async () => {
    localStorage.setItem(DEMO_ITSM_MODE_KEY, "znuny_readthrough");
    await renderPage();
    expect(await screen.findByTestId("cmdb-mode-notice")).toBeInTheDocument();
    expect(screen.queryByTestId("import-wizard")).not.toBeInTheDocument();
  });

  it("preselects the class from the URL and cannot continue without a file", async () => {
    await renderPage("/agent/cmdb/import?class=2");
    expect(await screen.findByTestId("import-class")).toHaveTextContent("Standort");
    expect(screen.getByTestId("import-next-1")).toBeDisabled();
  });

  it("maps an exported file 1:1 and shows samples from the first rows", async () => {
    await renderPage();
    await pickClassAndFile(csvFile(EXPORT_CSV));
    fireEvent.click(screen.getByTestId("import-next-1"));
    expect(await screen.findByTestId("mapping-editor")).toBeInTheDocument();
    expect(screen.getByTestId("mapping-target-0")).toHaveTextContent("CI-Nummer");
    expect(screen.getByTestId("mapping-target-2")).toHaveTextContent("Nicht importieren"); // the class column
    expect(screen.getByTestId("mapping-target-5")).toHaveTextContent("Hostname");
    expect(screen.getByTestId("mapping-target-6")).toHaveTextContent("IP-Adresse 1");
    expect(screen.getByTestId("mapping-samples-0")).toHaveTextContent("SRV-0001 · SRV-0002");
    expect(screen.queryByTestId("import-problems")).not.toBeInTheDocument();
    expect(screen.getByTestId("import-ident-number")).toBeChecked();
  });

  it("keeps the real import locked until a dry run of the same file and mapping succeeded", async () => {
    await renderPage();
    await pickClassAndFile(csvFile(EXPORT_CSV));
    fireEvent.click(screen.getByTestId("import-next-1"));
    fireEvent.click(await screen.findByTestId("import-next-2"));

    // no dry run yet
    expect(await screen.findByTestId("import-step-dry")).toBeInTheDocument();
    expect(screen.getByTestId("import-next-3")).toBeDisabled();
    expect(importRequests()).toHaveLength(0);

    // the demo's dry run finds one faulty row; with "abort" that blocks the import
    fireEvent.click(screen.getByTestId("import-dry-start"));
    expect(await screen.findByTestId("import-dry-count-errors")).toHaveTextContent("1");
    expect(screen.getByTestId("import-dry-count-created")).toHaveTextContent("3");
    expect(screen.getByTestId("import-dry-blocked")).toBeInTheDocument();
    expect(screen.getByTestId("import-next-3")).toBeDisabled();
    const rows = await screen.findAllByTestId("import-report-row");
    expect(rows).toHaveLength(5);
    expect(rows[3]).toHaveTextContent("ungültiges Format");
    expect(screen.getByTestId("import-report-download")).toHaveAttribute("href", expect.stringMatching(/\/api\/v1\/jobs\/\d+\/files\/2$/));
    expect(importRequests()[0].request).toMatchObject({ dry_run: true, class_id: 1, file_format: "csv" });
    expect(importRequests()[0].file.name).toBe("export.csv");

    // "skip faulty rows" changes the options, so the earlier dry run no longer counts
    fireEvent.click(screen.getByTestId("import-back-3"));
    fireEvent.click(await screen.findByTestId("import-on-error-skip"));
    fireEvent.click(screen.getByTestId("import-next-2"));
    expect(await screen.findByTestId("import-dry-stale")).toBeInTheDocument();
    expect(screen.getByTestId("import-next-3")).toBeDisabled();

    fireEvent.click(screen.getByTestId("import-dry-start"));
    await waitFor(() => expect(screen.getByTestId("import-next-3")).toBeEnabled());
    expect(screen.queryByTestId("import-dry-stale")).not.toBeInTheDocument();
    expect(importRequests()).toHaveLength(2);
    expect(importRequests()[1].request).toMatchObject({ dry_run: true, options: { on_error: "skip", identifiers: ["number"] } });

    // the real run
    fireEvent.click(screen.getByTestId("import-next-3"));
    expect(screen.getByTestId("import-run-summary")).toHaveTextContent("3 neu, 1 geändert, 1 fehlerhaft");
    fireEvent.click(screen.getByTestId("import-run-start"));
    expect(await screen.findByTestId("import-run-count-created")).toHaveTextContent("3");
    expect(screen.getByTestId("import-run-count-skipped")).toHaveTextContent("1");
    expect(importRequests()).toHaveLength(3);
    expect(importRequests()[2].request).toMatchObject({ dry_run: false });
    expect(importRequests()[2].request.mapping).toEqual(
      expect.arrayContaining([
        { column: "number", target: "number" },
        { column: "class", target: "ignore" },
        { column: "attr:hostname", target: "attr:hostname" },
      ]),
    );
    expect(screen.getByTestId("import-to-list")).toBeInTheDocument();
  });

  it("changing the mapping after the dry run locks the import again", async () => {
    await renderPage();
    await pickClassAndFile(csvFile(EXPORT_CSV));
    fireEvent.click(screen.getByTestId("import-next-1"));
    fireEvent.click(await screen.findByTestId("import-on-error-skip"));
    fireEvent.click(screen.getByTestId("import-next-2"));
    fireEvent.click(await screen.findByTestId("import-dry-start"));
    await waitFor(() => expect(screen.getByTestId("import-next-3")).toBeEnabled());

    fireEvent.click(screen.getByTestId("import-back-3"));
    fireEvent.click(await screen.findByTestId("mapping-target-6"));
    fireEvent.click(await screen.findByTestId("mapping-target-6-menu-option-"));
    fireEvent.click(screen.getByTestId("import-next-2"));
    expect(await screen.findByTestId("import-dry-stale")).toBeInTheDocument();
    expect(screen.getByTestId("import-next-3")).toBeDisabled();
  });

  it("blocks the dry run when the identifier column is not mapped", async () => {
    await renderPage();
    await pickClassAndFile(csvFile(EXPORT_CSV));
    fireEvent.click(screen.getByTestId("import-next-1"));
    // the identifier follows the mapping while nobody chose one: no number column, the name identifies
    fireEvent.click(await screen.findByTestId("mapping-target-0"));
    fireEvent.click(await screen.findByTestId("mapping-target-0-menu-option-"));
    expect(screen.queryByTestId("import-problems")).not.toBeInTheDocument();
    expect(screen.getByTestId("import-ident-name")).toBeChecked();
    // an identifier the user chose stays, and is flagged once its column goes away
    fireEvent.click(screen.getByTestId("import-ident-attr:hostname"));
    fireEvent.click(screen.getByTestId("mapping-target-5"));
    fireEvent.click(await screen.findByTestId("mapping-target-5-menu-option-"));
    expect(screen.getByTestId("import-problems")).toHaveTextContent("Kennung");
    expect(screen.getByTestId("import-next-2")).toBeDisabled();
  });

  it("offers a target only once", async () => {
    await renderPage();
    await pickClassAndFile(csvFile(EXPORT_CSV));
    fireEvent.click(screen.getByTestId("import-next-1"));
    fireEvent.click(await screen.findByTestId("mapping-target-2"));
    const taken = await screen.findByTestId("mapping-target-2-menu-option-name");
    expect(taken).toBeDisabled();
  });

  it("saves the mapping as a template and applies it to the next file", async () => {
    await renderPage();
    const odd = csvFile("Rechnername;Zusatz\nweb-01;x\nweb-02;y", "alt.csv");
    await pickClassAndFile(odd);
    fireEvent.click(screen.getByTestId("import-next-1"));
    expect(await screen.findByTestId("mapping-target-0")).toHaveTextContent("Nicht importieren");

    fireEvent.click(screen.getByTestId("mapping-target-0"));
    fireEvent.click(await screen.findByTestId("mapping-target-0-menu-option-name"));
    fireEvent.click(screen.getByTestId("mapping-target-1"));
    fireEvent.click(await screen.findByTestId("mapping-target-1-menu-option-attr:hostname"));
    fireEvent.change(screen.getByTestId("import-template-name"), { target: { value: "Altsystem" } });
    fireEvent.click(screen.getByTestId("import-template-save-btn"));
    await waitFor(() => expect(screen.getByTestId("import-template-msg")).toHaveTextContent("Altsystem"));
    expect(store.templates).toHaveLength(1);
    expect(store.templates[0].mapping).toEqual([
      { column: "Rechnername", target: "name" },
      { column: "Zusatz", target: "attr:hostname" },
    ]);
    expect(store.templates[0]).toMatchObject({ class_id: 1, file_format: "csv" });

    // back to step 1: pick the template for the same file, the editor shows the stored mapping
    fireEvent.click(screen.getByTestId("import-back-2"));
    fireEvent.click(await screen.findByTestId("import-template"));
    fireEvent.click(await screen.findByTestId(`import-template-menu-option-${store.templates[0].id}`));
    fireEvent.change(screen.getByTestId("import-file"), { target: { files: [csvFile("Zusatz;Rechnername\nq;srv-9", "neu.csv")] } });
    await waitFor(() => expect(screen.getByTestId("import-next-1")).toBeEnabled());
    fireEvent.click(screen.getByTestId("import-next-1"));
    expect(await screen.findByTestId("mapping-target-0")).toHaveTextContent("Hostname"); // column "Zusatz"
    expect(screen.getByTestId("mapping-target-1")).toHaveTextContent("Name"); // column "Rechnername"
  });

  it("sends no mapping for an Excel file and says the server reads its header", async () => {
    await renderPage();
    const xlsx = new File(["PK"], "inventar.xlsx", { type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" });
    await pickClassAndFile(xlsx);
    expect(screen.getByTestId("import-sheet")).toBeInTheDocument();
    expect(screen.queryByTestId("import-csv-options")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("import-next-1"));
    expect(await screen.findByTestId("import-xlsx-notice")).toBeInTheDocument();
    expect(screen.getByTestId("import-next-2")).toBeEnabled();
    fireEvent.click(screen.getByTestId("import-next-2"));
    fireEvent.click(await screen.findByTestId("import-dry-start"));
    await screen.findByTestId("import-dry-count-created");
    expect(importRequests()[0].request).toMatchObject({ file_format: "xlsx", mapping: null, dry_run: true });
  });

  it("shows the error and the report download when the import job fails", async () => {
    failRealJobs = true;
    await renderPage();
    await pickClassAndFile(csvFile(EXPORT_CSV));
    fireEvent.click(screen.getByTestId("import-next-1"));
    fireEvent.click(await screen.findByTestId("import-on-error-skip"));
    fireEvent.click(screen.getByTestId("import-next-2"));
    fireEvent.click(await screen.findByTestId("import-dry-start"));
    await waitFor(() => expect(screen.getByTestId("import-next-3")).toBeEnabled());
    fireEvent.click(screen.getByTestId("import-next-3"));
    fireEvent.click(screen.getByTestId("import-run-start"));

    expect(await screen.findByTestId("import-run-failed")).toHaveTextContent("fehlgeschlagen");
    expect(screen.getByTestId("import-run-progress-error")).toHaveTextContent("Boom");
    expect(await screen.findByTestId("import-report-download")).toBeInTheDocument();
    // the uploaded input file is no download
    expect(screen.queryByText(/input-import\.csv/)).not.toBeInTheDocument();
    expect(screen.queryByTestId("import-run-counts")).not.toBeInTheDocument();
  });

  it("downloads a header-only template for the class", async () => {
    const created: string[] = [];
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: (b: Blob) => (created.push(String(b.size)), "blob:x"), revokeObjectURL: () => {} }));
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    await renderPage();
    fireEvent.click(await screen.findByTestId("import-class"));
    fireEvent.click(await screen.findByTestId("import-class-menu-option-1"));
    await waitFor(() => expect(screen.getByTestId("import-download-template")).toBeEnabled());
    fireEvent.click(screen.getByTestId("import-download-template"));
    expect(click).toHaveBeenCalledTimes(1);
    expect(created).toHaveLength(1);
    expect(Number(created[0])).toBeGreaterThan(40);
  });
});
