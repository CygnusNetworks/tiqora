import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import i18n from "@/i18n";
import { createDemoCmdb, resolveCmdb, type DemoCmdb } from "@/demo/cmdbMock";
import { DEMO_ITSM_MODE_KEY } from "@/demo/itsmMode";
import { api, ApiError } from "@/lib/api";
import type { Ci, CmdbClass } from "@/lib/cmdbApi";
import { CiEditRoute, CiNewRoute } from "./CiEditPage";

let store: DemoCmdb;
let ciOverride: ((ci: Ci) => Ci) | null = null;
let classesOverride: ((c: CmdbClass[]) => CmdbClass[]) | null = null;
let putError: ApiError | null = null;
const requests: Array<{ method: string; path: string; body: unknown }> = [];
const bodyOf = (method: string, re: RegExp) => requests.filter((r) => r.method === method && re.test(r.path)).at(-1)?.body as Record<string, unknown> | undefined;

function serveDemo() {
  vi.spyOn(api, "request").mockImplementation(async (method: string, path: string, opts?: { body?: unknown; query?: Record<string, unknown> }) => {
    const query = new URLSearchParams();
    for (const [k, v] of Object.entries(opts?.query ?? {})) {
      if (v === undefined || v === null) continue;
      query.append(k, String(v));
    }
    requests.push({ method, path, body: opts?.body });
    if (method === "PUT" && putError) throw putError;
    const out = resolveCmdb(store, method, path, opts?.body, query);
    if (!out) throw new Error(`unmocked ${method} ${path}`);
    if (out.status >= 400) throw new ApiError(out.status, out.body, path);
    if (ciOverride && method === "GET" && /\/cis\/\d+$/.test(path)) return ciOverride(out.body as Ci) as never;
    if (classesOverride && method === "GET" && /\/cmdb\/classes$/.test(path)) return classesOverride(out.body as CmdbClass[]) as never;
    return out.body as never;
  });
}

async function renderEdit(initial: string) {
  const rootRoute = createRootRoute();
  const editRoute = createRoute({ getParentRoute: () => rootRoute, path: "/agent/cmdb/$ciId/edit", component: CiEditRoute });
  const newRoute = createRoute({
    getParentRoute: () => rootRoute,
    path: "/agent/cmdb/new",
    validateSearch: (s: Record<string, unknown>): { class?: number } => {
      const n = Number(s.class);
      return Number.isInteger(n) && n > 0 ? { class: n } : {};
    },
    component: CiNewRoute,
  });
  const detailRoute = createRoute({ getParentRoute: () => rootRoute, path: "/agent/cmdb/$ciId", component: () => <div data-testid="detail-stub" /> });
  const listRoute = createRoute({ getParentRoute: () => rootRoute, path: "/agent/cmdb", component: () => <div data-testid="list-stub" /> });
  const router = createRouter({
    routeTree: rootRoute.addChildren([editRoute, newRoute, detailRoute, listRoute]),
    history: createMemoryHistory({ initialEntries: [initial] }),
  });
  await router.load();
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
        <RouterProvider router={router as any} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return router;
}

beforeEach(async () => {
  store = createDemoCmdb();
  ciOverride = null;
  classesOverride = null;
  putError = null;
  requests.length = 0;
  await i18n.changeLanguage("de");
  serveDemo();
});
afterEach(() => {
  vi.restoreAllMocks();
  localStorage.removeItem(DEMO_ITSM_MODE_KEY);
});

const save = () => fireEvent.click(screen.getByRole("button", { name: "Speichern" }));

describe("CiEditPage", () => {
  it("loads the CI into the form and saves with the lock version", async () => {
    const router = await renderEdit("/agent/cmdb/1/edit");
    const hostname = await screen.findByTestId("ci-attr-hostname-1-input");
    expect(hostname).toHaveValue("web-01.example.org");
    expect(screen.getByTestId("ci-edit-name")).toHaveValue("web-01.example.org");
    fireEvent.change(screen.getByTestId("ci-edit-name"), { target: { value: "web-01-neu.example.org" } });
    fireEvent.change(screen.getByTestId("ci-edit-note"), { target: { value: "Umbenannt" } });
    save();
    await waitFor(() => expect(router.state.location.pathname).toBe("/agent/cmdb/1"));
    expect(bodyOf("PUT", /\/cis\/1$/)).toMatchObject({ name: "web-01-neu.example.org", change_note: "Umbenannt", expected_lock_version: 2 });
    expect(bodyOf("PUT", /\/cis\/1$/)).toMatchObject({ drop_unknown: false });
    expect(store.cis.find((c) => c.id === 1)?.name).toBe("web-01-neu.example.org");
  });

  it("warns about unknown values and sends drop_unknown only after confirmation", async () => {
    ciOverride = (ci) => ({ ...ci, unknown_keys: ["CPU"] });
    const router = await renderEdit("/agent/cmdb/1/edit");
    expect(await screen.findByText(/kennt die Klassendefinition nicht mehr/)).toBeInTheDocument();
    expect(screen.getByTestId("ci-edit-unknown")).toHaveTextContent("CPU");
    fireEvent.change(screen.getByTestId("ci-edit-name"), { target: { value: "web-01b.example.org" } });
    save();
    expect(await screen.findByText(/Bitte bestätigen Sie/)).toBeInTheDocument();
    expect(requests.some((r) => r.method === "PUT")).toBe(false);
    fireEvent.click(screen.getByLabelText("Werte entfernen"));
    save();
    await waitFor(() => expect(router.state.location.pathname).toBe("/agent/cmdb/1"));
    expect(bodyOf("PUT", /\/cis\/1$/)).toMatchObject({ drop_unknown: true });
  });

  it("asks for the same confirmation when the server reports unknown attributes", async () => {
    putError = new ApiError(422, { detail: { code: "unknown_attributes", keys: ["RAM"] } }, "/api/v1/cmdb/cis/1");
    await renderEdit("/agent/cmdb/1/edit");
    await screen.findByTestId("ci-edit-name");
    fireEvent.change(screen.getByTestId("ci-edit-name"), { target: { value: "web-01c.example.org" } });
    save();
    expect(await screen.findByTestId("ci-edit-unknown")).toHaveTextContent("RAM");
    expect(screen.getByTestId("ci-edit-unknown-confirm")).toBeInTheDocument();
    expect(screen.getByLabelText("Werte entfernen")).not.toBeChecked();
  });

  it("maps 422 issues onto the inputs and shows a summary", async () => {
    putError = new ApiError(422, { detail: { code: "invalid_values", issues: [{ path: "ip[1]", code: "bad_format", message: "x" }, { path: "hostname[1]", code: "too_long", message: "y" }] } }, "/api/v1/cmdb/cis/1");
    await renderEdit("/agent/cmdb/1/edit");
    await screen.findByTestId("ci-attr-ip-1-input");
    fireEvent.change(screen.getByTestId("ci-edit-name"), { target: { value: "web-01d.example.org" } });
    save();
    const err = await screen.findByText("Der Wert hat ein ungültiges Format.");
    expect(err.closest("[data-testid^='ci-attr-ip-1']")).not.toBeNull();
    expect(screen.getByText("Der Wert ist zu lang.")).toBeInTheDocument();
    expect(screen.getByTestId("ci-edit-summary")).toHaveTextContent("2 Fehler");
  });

  it("blocks saving a required attribute that is empty, before any request", async () => {
    await renderEdit("/agent/cmdb/1/edit");
    const hostname = await screen.findByTestId("ci-attr-hostname-1-input");
    fireEvent.change(hostname, { target: { value: "" } });
    save();
    expect(await screen.findByText("Pflichtfeld.")).toBeInTheDocument();
    expect(requests.some((r) => r.method === "PUT")).toBe(false);
  });

  it("shows the conflict dialog on 409 and reloads the CI without keeping the input", async () => {
    await renderEdit("/agent/cmdb/1/edit");
    await screen.findByTestId("ci-edit-name");
    fireEvent.change(screen.getByTestId("ci-edit-name"), { target: { value: "meine-aenderung.example.org" } });
    store.cis.find((c) => c.id === 1)!.lockVersion += 1; // somebody else saved first
    save();
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Inzwischen geändert");
    fireEvent.click(screen.getByRole("button", { name: "Neu laden" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId("ci-edit-name")).toHaveValue("web-01.example.org"));
    fireEvent.change(screen.getByTestId("ci-edit-name"), { target: { value: "zweiter-versuch.example.org" } });
    save();
    await waitFor(() => expect(store.cis.find((c) => c.id === 1)?.name).toBe("zweiter-versuch.example.org"));
  });

  it("keeps the input when the conflict dialog is cancelled", async () => {
    await renderEdit("/agent/cmdb/1/edit");
    await screen.findByTestId("ci-edit-name");
    fireEvent.change(screen.getByTestId("ci-edit-name"), { target: { value: "bleibt.example.org" } });
    store.cis.find((c) => c.id === 1)!.lockVersion += 1;
    save();
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Abbrechen" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(screen.getByTestId("ci-edit-name")).toHaveValue("bleibt.example.org");
  });

  it("answers 'Keine Änderungen' and stays when nothing changed", async () => {
    const router = await renderEdit("/agent/cmdb/1/edit");
    await screen.findByTestId("ci-attr-hostname-1-input");
    save();
    expect(await screen.findByTestId("ci-edit-info")).toHaveTextContent("Keine Änderungen");
    expect(router.state.location.pathname).toBe("/agent/cmdb/1/edit");
  });

  it("creates with staged attachments", async () => {
    classesOverride = (cs) =>
      cs.map((c) =>
        c.id === 1 && c.definition
          ? { ...c, definition: { ...c.definition, attributes: [...c.definition.attributes, { key: "doc", name: "Dokument", type: "attachment", count_min: 0, count_max: 1, count_default: 1, required: false, searchable: false, portal: false, options: {} } as never] } }
          : c,
      );
    const router = await renderEdit("/agent/cmdb/new?class=1");
    await screen.findByTestId("ci-edit-title");
    expect(screen.getByTestId("ci-edit-title")).toHaveTextContent("Neues CI: Server");
    fireEvent.change(screen.getByTestId("ci-edit-name"), { target: { value: "app-01.example.org" } });
    fireEvent.change(await screen.findByTestId("ci-attr-hostname-1-input"), { target: { value: "app-01.example.org" } });
    const file = new File(["inhalt"], "handbuch.txt", { type: "text/plain" });
    fireEvent.change(await screen.findByTestId("ci-attr-doc-1-input-file"), { target: { files: [file] } });
    await waitFor(() => expect(requests.some((r) => r.method === "POST" && r.path.endsWith("/attachments"))).toBe(true));
    const upload = requests.find((r) => r.method === "POST" && r.path.endsWith("/attachments"))!.body as FormData;
    expect(upload.has("ci_id")).toBe(false); // staged, not bound
    save();
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/agent\/cmdb\/\d+$/));
    expect(bodyOf("POST", /\/cmdb\/cis$/)).toMatchObject({ class_id: 1, name: "app-01.example.org", attachment_ids: [1] });
    expect(store.cis.some((c) => c.name === "app-01.example.org")).toBe(true);
  });

  it("binds attachments to the CI when editing", async () => {
    classesOverride = (cs) =>
      cs.map((c) =>
        c.id === 1 && c.definition
          ? { ...c, definition: { ...c.definition, attributes: [...c.definition.attributes, { key: "doc", name: "Dokument", type: "attachment", count_min: 0, count_max: 1, count_default: 1, required: false, searchable: false, portal: false, options: {} } as never] } }
          : c,
      );
    await renderEdit("/agent/cmdb/1/edit");
    fireEvent.change(await screen.findByTestId("ci-attr-doc-1-input-file"), { target: { files: [new File(["x"], "a.txt")] } });
    await waitFor(() => expect(requests.some((r) => r.method === "POST" && r.path.endsWith("/attachments"))).toBe(true));
    const upload = requests.find((r) => r.method === "POST" && r.path.endsWith("/attachments"))!.body as FormData;
    expect(upload.get("ci_id")).toBe("1");
  });

  it("offers only writable, valid classes when no class is given", async () => {
    classesOverride = (cs) => cs.map((c) => (c.id === 2 ? { ...c, can_write: false } : c));
    const router = await renderEdit("/agent/cmdb/new");
    expect(await screen.findByTestId("ci-edit-class-1")).toHaveTextContent("Server");
    expect(screen.queryByTestId("ci-edit-class-2")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("ci-edit-class-1"));
    await waitFor(() => expect(router.state.location.search).toEqual({ class: 1 }));
    expect(await screen.findByTestId("ci-edit-title")).toHaveTextContent("Neues CI: Server");
  });

  it("asks before leaving with unsaved changes", async () => {
    const router = await renderEdit("/agent/cmdb/1/edit");
    await screen.findByTestId("ci-edit-name");
    fireEvent.click(screen.getByTestId("ci-edit-cancel")); // clean: leaves at once
    await waitFor(() => expect(router.state.location.pathname).toBe("/agent/cmdb/1"));
    await router.navigate({ to: "/agent/cmdb/$ciId/edit", params: { ciId: "1" } });
    await screen.findByTestId("ci-edit-name");
    fireEvent.change(screen.getByTestId("ci-edit-name"), { target: { value: "geaendert.example.org" } });
    fireEvent.click(screen.getByTestId("ci-edit-cancel"));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("Änderungen verwerfen?");
    fireEvent.click(screen.getByRole("button", { name: "Bleiben" }));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    expect(router.state.location.pathname).toBe("/agent/cmdb/1/edit");
    fireEvent.click(screen.getByTestId("ci-edit-cancel"));
    fireEvent.click(await screen.findByRole("button", { name: "Verlassen" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/agent/cmdb/1"));
  });

  it("shows a notice instead of the form when the CI may not be edited", async () => {
    ciOverride = (ci) => ({ ...ci, can_write: false });
    await renderEdit("/agent/cmdb/1/edit");
    expect(await screen.findByTestId("ci-edit-forbidden")).toHaveTextContent("nicht bearbeiten");
    expect(screen.queryByTestId("ci-edit-name")).not.toBeInTheDocument();
  });
});
