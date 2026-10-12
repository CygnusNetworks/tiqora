import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { DEMO_ITSM_MODE_KEY } from "@/demo/itsmMode";
import { type DemoCmdb } from "@/demo/cmdbMock";
import { CmdbCatalogPage } from "./CmdbCatalogPage";
import { createDemoCmdb, renderAdminPage, serveAdminDemo, type Recorded } from "./cmdbAdminTestUtils";

let store: DemoCmdb;
let requests: Recorded[];
const PATH = "/admin/cmdb/catalog";

beforeEach(() => {
  store = createDemoCmdb();
  requests = [];
  serveAdminDemo(store, requests);
});
afterEach(() => {
  vi.restoreAllMocks();
  localStorage.removeItem(DEMO_ITSM_MODE_KEY);
});

describe("CmdbCatalogPage", () => {
  it("lists the catalog classes and the items of the selected one", async () => {
    await renderAdminPage(CmdbCatalogPage, PATH);
    await screen.findByTestId("cmdb-catalog-class-CMDB::OS");
    fireEvent.click(screen.getByTestId("cmdb-catalog-class-CMDB::OS"));
    const row = await screen.findByTestId("cmdb-catalog-row-debian");
    expect(row).toHaveTextContent("Debian");
    expect(screen.queryByTestId("cmdb-catalog-row-planned")).not.toBeInTheDocument();
  });

  it("offers a functionality select only for the two state classes", async () => {
    await renderAdminPage(CmdbCatalogPage, PATH);
    fireEvent.click(await screen.findByTestId("cmdb-catalog-class-ITSM::Core::IncidentState"));
    fireEvent.click(await screen.findByTestId("cmdb-catalog-new"));
    expect(await screen.findByTestId("cmdb-catalog-functionality")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Cancel"));
    await waitFor(() => expect(screen.queryByTestId("cmdb-catalog-name")).not.toBeInTheDocument());

    fireEvent.click(screen.getByTestId("cmdb-catalog-class-ITSM::ConfigItem::DeploymentState"));
    fireEvent.click(screen.getByTestId("cmdb-catalog-new"));
    expect(await screen.findByTestId("cmdb-catalog-functionality")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Cancel"));
    await waitFor(() => expect(screen.queryByTestId("cmdb-catalog-name")).not.toBeInTheDocument());

    fireEvent.click(screen.getByTestId("cmdb-catalog-class-CMDB::OS"));
    fireEvent.click(screen.getByTestId("cmdb-catalog-new"));
    await screen.findByTestId("cmdb-catalog-name");
    expect(screen.queryByTestId("cmdb-catalog-functionality")).not.toBeInTheDocument();
  });

  it("shows the functionality column only for state classes", async () => {
    await renderAdminPage(CmdbCatalogPage, PATH);
    fireEvent.click(await screen.findByTestId("cmdb-catalog-class-ITSM::Core::IncidentState"));
    const row = await screen.findByTestId("cmdb-catalog-row-warning");
    expect(row).toHaveTextContent("Warning");
    expect(screen.getAllByRole("columnheader")).toHaveLength(5);
    fireEvent.click(screen.getByTestId("cmdb-catalog-class-CMDB::OS"));
    await screen.findByTestId("cmdb-catalog-row-debian");
    expect(screen.getAllByRole("columnheader")).toHaveLength(4);
  });

  it("keeps the key read-only on edit and saves with PUT", async () => {
    await renderAdminPage(CmdbCatalogPage, PATH);
    fireEvent.click(await screen.findByTestId("cmdb-catalog-class-CMDB::OS"));
    fireEvent.click(await screen.findByTestId("cmdb-catalog-edit-debian"));
    const key = await screen.findByTestId("cmdb-catalog-key");
    expect(key).toHaveValue("debian");
    expect(key).toHaveAttribute("readonly");
    fireEvent.change(screen.getByTestId("cmdb-catalog-name"), { target: { value: "Debian GNU/Linux" } });
    fireEvent.click(screen.getByTestId("cmdb-catalog-save"));
    await waitFor(() => expect(screen.queryByTestId("cmdb-catalog-name")).not.toBeInTheDocument());
    const put = requests.find((r) => r.method === "PUT" && /\/catalog\/21$/.test(r.path));
    expect(put?.body).toMatchObject({ catalog_class: "CMDB::OS", item_key: "debian", name: "Debian GNU/Linux", functionality: null });
    expect(await screen.findByTestId("cmdb-catalog-row-debian")).toHaveTextContent("Debian GNU/Linux");
  });

  it("creates an incident state with its functionality", async () => {
    await renderAdminPage(CmdbCatalogPage, PATH);
    fireEvent.click(await screen.findByTestId("cmdb-catalog-class-ITSM::Core::IncidentState"));
    fireEvent.click(await screen.findByTestId("cmdb-catalog-new"));
    fireEvent.change(await screen.findByTestId("cmdb-catalog-name"), { target: { value: "Eingeschränkt" } });
    fireEvent.change(screen.getByTestId("cmdb-catalog-key"), { target: { value: "degraded" } });
    expect(screen.getByTestId("cmdb-catalog-save")).toBeDisabled();
    fireEvent.click(screen.getByTestId("cmdb-catalog-functionality"));
    fireEvent.click(await screen.findByTestId("cmdb-catalog-functionality-menu-option-warning"));
    fireEvent.click(screen.getByTestId("cmdb-catalog-save"));
    await waitFor(() => expect(requests.some((r) => r.method === "POST" && r.path.endsWith("/admin/cmdb/catalog"))).toBe(true));
    const post = requests.find((r) => r.method === "POST" && r.path.endsWith("/admin/cmdb/catalog"));
    expect(post?.body).toMatchObject({ catalog_class: "ITSM::Core::IncidentState", item_key: "degraded", functionality: "warning", valid: true });
  });

  it("shows only the mode notice outside Mode B", async () => {
    localStorage.setItem(DEMO_ITSM_MODE_KEY, "znuny_readthrough");
    await renderAdminPage(CmdbCatalogPage, PATH);
    expect(await screen.findByTestId("cmdb-mode-notice")).toBeInTheDocument();
    expect(screen.queryByTestId("cmdb-catalog-classes")).not.toBeInTheDocument();
    expect(requests.filter((r) => r.path.endsWith("/catalog-classes"))).toHaveLength(0);
  });
});
