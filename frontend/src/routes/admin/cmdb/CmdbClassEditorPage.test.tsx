import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { QueryClient } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { cmdbAdminApi } from "@/lib/cmdbAdminApi";
import { DEMO_ITSM_MODE_KEY } from "@/demo/itsmMode";
import { type DemoCmdb } from "@/demo/cmdbMock";
import { CmdbClassEditorPage } from "./CmdbClassEditorPage";
import { createDemoCmdb, renderAdminRoute, serveAdminDemo, type Recorded } from "./cmdbAdminTestUtils";

let store: DemoCmdb;
let requests: Recorded[];

beforeEach(async () => {
  store = createDemoCmdb();
  requests = [];
  await i18n.changeLanguage("de");
});
afterEach(() => {
  vi.restoreAllMocks();
  localStorage.removeItem(DEMO_ITSM_MODE_KEY);
});

const ROUTE = "/admin/cmdb/classes/$classId";
const open = (id: number) => renderAdminRoute(CmdbClassEditorPage, ROUTE, `/admin/cmdb/classes/${id}`);
const post = (suffix: string) => requests.filter((r) => r.method === "POST" && r.path.endsWith(suffix));

/** Row of the attribute with this key (uids are global counters, so look the row up by its text). */
async function rowOf(key: string) {
  const tree = await screen.findByTestId("def-tree");
  const btn = within(tree).getAllByRole("button").find((b) => b.getAttribute("data-testid")?.startsWith("def-select-") && b.textContent?.includes(key));
  if (!btn) throw new Error(`no row ${key}`);
  return btn;
}

describe("CmdbClassEditorPage", () => {
  it("shows the class fields with a read-only key and the attribute tree of the current definition", async () => {
    serveAdminDemo(store, requests);
    await open(1);
    expect(await screen.findByTestId("cls-key")).toHaveValue("server");
    expect(screen.getByTestId("cls-key")).toBeDisabled();
    expect(screen.getByTestId("cls-name")).toHaveValue("Server");
    expect(screen.getByTestId("cls-prefix")).toHaveValue("SRV");
    for (const key of ["hostname", "ip", "os", "location"]) expect(await rowOf(key)).toBeInTheDocument();
    expect(screen.getByTestId("def-version-1")).toBeInTheDocument();
    expect(screen.getByTestId("def-save-version")).toBeDisabled();
  });

  it("saves the class fields with the expected lock version", async () => {
    serveAdminDemo(store, requests);
    await open(1);
    fireEvent.change(await screen.findByTestId("cls-name"), { target: { value: "Server (physisch)" } });
    fireEvent.change(screen.getByTestId("cls-prefix"), { target: { value: "SV-" } });
    fireEvent.click(screen.getByTestId("cls-save"));
    await screen.findByText(i18n.t("admin.cmdb.editor.classSaved"));
    const put = requests.find((r) => r.method === "PUT" && r.path.endsWith("/classes/1"));
    expect(put?.body).toMatchObject({ class_key: "server", name: "Server (physisch)", number_prefix: "SV-", group_id: 1, expected_lock_version: 1, portal_visible: true, valid: true });
    await waitFor(() => expect(screen.getByTestId("cls-save")).toBeDisabled());
    expect(screen.getByTestId("cls-name")).toHaveValue("Server (physisch)");
  });

  it("shows the conflict message when the class changed meanwhile", async () => {
    serveAdminDemo(store, requests);
    vi.spyOn(cmdbAdminApi, "updateClass").mockRejectedValue(new ApiError(409, { detail: { code: "conflict", message: "Die Klasse wurde zwischenzeitlich geändert." } }, "/api/v1/admin/cmdb/classes/2"));
    await open(2);
    fireEvent.change(await screen.findByTestId("cls-name"), { target: { value: "Standort 2" } });
    fireEvent.click(screen.getByTestId("cls-save"));
    expect(await screen.findByTestId("cls-error")).toHaveTextContent("zwischenzeitlich");
  });

  it("jumps from a server issue to the attribute", async () => {
    serveAdminDemo(store, requests, (method, path) =>
      method === "POST" && path.endsWith("/definitions/validate") ? [{ path: "location", code: "unknown_class", message: "Die Klasse „site2“ gibt es nicht." }] : undefined,
    );
    await open(1);
    fireEvent.click(await screen.findByTestId("def-validate"));
    const list = await screen.findByTestId("def-issues");
    expect(list).toHaveTextContent("site2");
    expect(post("/definitions/validate")[0].body).toMatchObject({ class_id: 1 });
    fireEvent.click(screen.getByTestId("def-issue-jump-0"));
    expect(await screen.findByTestId("def-key")).toHaveValue("location");
    expect(screen.getByTestId("def-panel-issue")).toHaveTextContent("site2");
  });

  it("reports a valid definition", async () => {
    serveAdminDemo(store, requests);
    await open(1);
    fireEvent.click(await screen.findByTestId("def-validate"));
    expect(await screen.findByTestId("def-valid")).toBeInTheDocument();
  });

  it("fills the editor from an import without saving anything", async () => {
    serveAdminDemo(store, requests);
    await open(1);
    fireEvent.click(await screen.findByTestId("def-import-open"));
    fireEvent.change(await screen.findByTestId("def-import-text"), { target: { value: "[ ... ]" } });
    fireEvent.click(screen.getByTestId("def-import-preview"));
    expect(await screen.findByTestId("def-import-warnings")).toHaveTextContent("Demo");
    expect(screen.getByTestId("def-import-missing")).toHaveTextContent("ITSM::Demo::Type");
    fireEvent.click(screen.getByTestId("def-import-apply"));
    expect(await rowOf("Typ")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByTestId("def-import-result")).not.toBeInTheDocument());
    expect(screen.getByTestId("def-save-version")).toBeEnabled();
    expect(requests.filter((r) => r.method !== "GET" && !r.path.endsWith("/definition-import"))).toEqual([]);
  });

  it("blocks saving a version while an attribute has a local problem", async () => {
    serveAdminDemo(store, requests);
    await open(1);
    fireEvent.click(await screen.findByTestId("def-add"));
    fireEvent.change(await screen.findByTestId("def-key"), { target: { value: "hostname" } });
    fireEvent.change(screen.getByTestId("def-name"), { target: { value: "Doppelt" } });
    expect(screen.getByTestId("def-local-issues")).toBeInTheDocument();
    expect(screen.getByTestId("def-save-version")).toBeDisabled();
    expect(screen.getByTestId("def-panel-issue")).toHaveTextContent("mehrfach");
  });

  it("saves the edited tree as a new version and lists its changes against the previous one", async () => {
    serveAdminDemo(store, requests);
    const invalidate = vi.spyOn(QueryClient.prototype, "invalidateQueries");
    await open(2);
    fireEvent.click(await screen.findByTestId("def-add"));
    fireEvent.change(await screen.findByTestId("def-key"), { target: { value: "phone" } });
    fireEvent.change(screen.getByTestId("def-name"), { target: { value: "Telefon" } });
    fireEvent.click(screen.getByTestId("def-searchable"));
    const contactUid = (await rowOf("contact")).getAttribute("data-testid")!.replace("def-select-", "");
    fireEvent.click(screen.getByTestId(`def-remove-${contactUid}`));
    fireEvent.click(screen.getByTestId("def-save-version"));
    fireEvent.change(await screen.findByTestId("def-comment"), { target: { value: "Telefon statt Kontakt" } });
    fireEvent.click(screen.getByTestId("def-version-confirm"));
    await screen.findByTestId("def-version-2");

    const body = post("/classes/2/definitions")[0].body as { attributes: Array<{ key: string; name: string; searchable: boolean }>; comment: string };
    expect(body.comment).toBe("Telefon statt Kontakt");
    expect(body.attributes.map((a) => a.key)).toEqual(["address", "phone"]);
    expect(body.attributes[1]).toMatchObject({ name: "Telefon", searchable: true, type: "text" });
    expect(screen.queryByTestId("def-version-1")).toBeInTheDocument();
    // the agent side (new-CI form, filters) must not keep the old definition for a minute
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["cmdb"] });

    fireEvent.click(screen.getByTestId("def-diff-toggle-2"));
    const diff = await screen.findByTestId("def-diff-2");
    expect(diff).toHaveTextContent("Hinzugefügt");
    expect(diff).toHaveTextContent("phone");
    expect(diff).toHaveTextContent("Entfernt");
    expect(diff).toHaveTextContent("contact");
    expect(screen.queryByTestId("def-diff-toggle-1")).not.toBeInTheDocument();
  });
});
