import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor } from "@testing-library/react";
import { DEMO_ITSM_MODE_KEY } from "@/demo/itsmMode";
import { type DemoCmdb } from "@/demo/cmdbMock";
import { exampleNumber, suggestClassKey } from "@/components/cmdb/adminModel";
import { CmdbClassesPage } from "./CmdbClassesPage";
import { createDemoCmdb, renderAdminPage, serveAdminDemo, type Recorded } from "./cmdbAdminTestUtils";

let store: DemoCmdb;
let requests: Recorded[];

beforeEach(() => {
  store = createDemoCmdb();
  requests = [];
});
afterEach(() => {
  vi.restoreAllMocks();
  localStorage.removeItem(DEMO_ITSM_MODE_KEY);
});

const PATH = "/admin/cmdb/classes";

describe("CmdbClassesPage", () => {
  it("lists the classes with key, name, group and validity", async () => {
    serveAdminDemo(store, requests);
    await renderAdminPage(CmdbClassesPage, PATH);
    const row = await screen.findByTestId("cmdb-class-row-server");
    expect(row).toHaveTextContent("server");
    expect(row).toHaveTextContent("Server");
    expect(row).toHaveTextContent("users");
    expect(screen.getByTestId("cmdb-class-row-site")).toBeInTheDocument();
    expect(screen.queryByTestId("cmdb-seed-banner")).not.toBeInTheDocument();
  });

  it("applies a template via the route and opens the new class", async () => {
    serveAdminDemo(store, requests);
    const router = await renderAdminPage(CmdbClassesPage, PATH);
    fireEvent.click(await screen.findByTestId("cmdb-from-template"));
    const tpl = await screen.findByTestId("cmdb-template-network");
    expect(screen.getAllByRole("radio")).toHaveLength(5);
    expect(screen.getByTestId("cmdb-template-apply")).toBeDisabled();
    fireEvent.click(tpl);
    fireEvent.click(screen.getByTestId("cmdb-template-group"));
    fireEvent.click(await screen.findByTestId("cmdb-template-group-menu-option-2"));
    fireEvent.click(screen.getByTestId("cmdb-template-apply"));
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/admin\/cmdb\/classes\/\d+$/));
    const call = requests.find((r) => r.method === "POST" && r.path.endsWith("/class-templates/network/apply"));
    expect(call?.body).toEqual({ group_id: 2 });
  });

  it("creates a class from the dialog and navigates to it", async () => {
    serveAdminDemo(store, requests);
    const router = await renderAdminPage(CmdbClassesPage, PATH);
    fireEvent.click(await screen.findByTestId("cmdb-new-class"));
    fireEvent.change(await screen.findByTestId("cmdb-class-name"), { target: { value: "Drucker" } });
    expect(screen.getByTestId("cmdb-class-key")).toHaveValue("drucker");
    expect(screen.getByTestId("cmdb-class-prefix-example")).toHaveTextContent("DRUCKER-000001");
    fireEvent.change(screen.getByTestId("cmdb-class-prefix"), { target: { value: "PRN-" } });
    expect(screen.getByTestId("cmdb-class-prefix-example")).toHaveTextContent("PRN-000001");
    expect(screen.getByTestId("cmdb-class-save")).toBeDisabled();
    fireEvent.click(screen.getByTestId("cmdb-class-group"));
    fireEvent.click(await screen.findByTestId("cmdb-class-group-menu-option-1"));
    fireEvent.click(screen.getByTestId("cmdb-class-portal"));
    fireEvent.click(screen.getByTestId("cmdb-class-save"));
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/admin\/cmdb\/classes\/\d+$/));
    const call = requests.find((r) => r.method === "POST" && r.path.endsWith("/admin/cmdb/classes"));
    expect(call?.body).toMatchObject({ class_key: "drucker", name: "Drucker", group_id: 1, number_prefix: "PRN-", portal_visible: true });
  });

  it("rejects a malformed class key", async () => {
    serveAdminDemo(store, requests);
    await renderAdminPage(CmdbClassesPage, PATH);
    fireEvent.click(await screen.findByTestId("cmdb-new-class"));
    fireEvent.change(await screen.findByTestId("cmdb-class-name"), { target: { value: "X" } });
    fireEvent.change(screen.getByTestId("cmdb-class-key"), { target: { value: "Bad Key" } });
    fireEvent.click(screen.getByTestId("cmdb-class-group"));
    fireEvent.click(await screen.findByTestId("cmdb-class-group-menu-option-1"));
    expect(screen.getByTestId("cmdb-class-save")).toBeDisabled();
    expect(screen.getByTestId("cmdb-class-key")).toHaveAttribute("aria-invalid", "true");
  });

  it("offers the defaults when the catalog is not seeded and calls seed-defaults", async () => {
    let seeded = false;
    serveAdminDemo(store, requests, (method, path) => {
      if (path.endsWith("/admin/cmdb/status")) {
        return { mode: "tiqora", views_present: seeded, catalog_seeded: seeded, class_count: 0, ci_count: 0, archived_version_count: 0 };
      }
      if (method === "POST" && path.endsWith("/seed-defaults")) {
        seeded = true;
        return { mode: "tiqora", views_present: true, catalog_seeded: true, class_count: 0, ci_count: 0, archived_version_count: 0 };
      }
      return undefined;
    });
    await renderAdminPage(CmdbClassesPage, PATH);
    const banner = await screen.findByTestId("cmdb-seed-banner");
    expect(banner).toHaveTextContent("standard catalog");
    fireEvent.click(screen.getByTestId("cmdb-seed-defaults"));
    await waitFor(() => expect(screen.queryByTestId("cmdb-seed-banner")).not.toBeInTheDocument());
    expect(requests.filter((r) => r.method === "POST" && r.path.endsWith("/admin/cmdb/seed-defaults"))).toHaveLength(1);
  });

  it("shows the banner when only the views are missing", async () => {
    serveAdminDemo(store, requests, (_m, path) =>
      path.endsWith("/admin/cmdb/status")
        ? { mode: "tiqora", views_present: false, catalog_seeded: true, class_count: 2, ci_count: 0, archived_version_count: 0 }
        : undefined,
    );
    await renderAdminPage(CmdbClassesPage, PATH);
    const banner = await screen.findByTestId("cmdb-seed-banner");
    expect(banner).toHaveTextContent("database views");
  });

  it("links to settings and to the ITSM link-type page instead of editing link types", async () => {
    serveAdminDemo(store, requests);
    await renderAdminPage(CmdbClassesPage, PATH);
    expect(await screen.findByTestId("cmdb-settings-link")).toHaveAttribute("href", "/admin/settings/cmdb");
    expect(screen.getByTestId("cmdb-linktypes-link")).toHaveAttribute("href", "/admin/itsm/link-types");
  });

  it("starts the full recompute of the incident states and shows its job", async () => {
    serveAdminDemo(store, requests);
    await renderAdminPage(CmdbClassesPage, PATH);
    fireEvent.click(await screen.findByTestId("cmdb-recompute"));
    await waitFor(() =>
      expect(requests.some((r) => r.method === "POST" && r.path.endsWith("/admin/cmdb/propagation/recompute"))).toBe(true),
    );
    expect(await screen.findByTestId("cmdb-recompute-job-close")).toBeInTheDocument();
  });

  it("shows only the mode notice outside Mode B and never calls the gated routes", async () => {
    localStorage.setItem(DEMO_ITSM_MODE_KEY, "znuny_readthrough");
    serveAdminDemo(store, requests);
    await renderAdminPage(CmdbClassesPage, PATH);
    expect(await screen.findByTestId("cmdb-mode-notice")).toBeInTheDocument();
    expect(screen.queryByTestId("cmdb-new-class")).not.toBeInTheDocument();
    expect(requests.filter((r) => r.path.endsWith("/admin/cmdb/classes"))).toHaveLength(0);
  });
});

describe("class helpers", () => {
  it("suggests a key from the name", () => {
    expect(suggestClassKey("Mein Server")).toBe("mein_server");
    expect(suggestClassKey("Größe")).toBe("groesse");
    expect(suggestClassKey("123")).toBe("");
  });
  it("builds the example number like the backend", () => {
    expect(exampleNumber("", "server")).toBe("SERVER-000001");
    expect(exampleNumber("SRV-", "server")).toBe("SRV-000001");
  });
});
