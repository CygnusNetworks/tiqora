import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { ApiError } from "@/lib/api";
import { renderChangeAdmin, serveChangeAdmin, useGerman, type Recorded } from "./changeAdminTestUtils";
import { ChangeAdminPage } from "./ChangeAdminPage";

beforeAll(useGerman);
afterEach(() => vi.restoreAllMocks());

const ROUTE = "/admin/itsm/change";
const BASE = "/api/v1/admin/itsm/change";

describe("ChangeAdminPage", () => {
  it("has the five tabs and links to the settings section", async () => {
    serveChangeAdmin();
    await renderChangeAdmin(ChangeAdminPage, ROUTE);
    const tabs = screen.getAllByRole("tab").map((t) => t.textContent);
    expect(tabs).toEqual(["Vorlagen", "CAB", "Risikofragen", "Sperrzeiträume", "Change-Archiv"]);
    expect(screen.getByRole("tab", { name: "Vorlagen" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("change-admin-settings")).toHaveAttribute("href", "/admin/settings/itsm_change");
  });

  it("switches between the tabs", async () => {
    serveChangeAdmin();
    await renderChangeAdmin(ChangeAdminPage, ROUTE);
    fireEvent.click(screen.getByRole("tab", { name: "CAB" }));
    expect(await screen.findByTestId("change-cab-tab")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Risikofragen" }));
    expect(await screen.findByTestId("change-risk-tab")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Sperrzeiträume" }));
    expect(await screen.findByTestId("change-freeze-tab")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Change-Archiv" }));
    expect(await screen.findByTestId("change-archive-tab")).toBeInTheDocument();
    expect(screen.queryByTestId("change-templates-tab")).toBeNull();
  });

  it("opens a tab named in the address", async () => {
    serveChangeAdmin();
    await renderChangeAdmin(ChangeAdminPage, ROUTE, `${ROUTE}?tab=freeze`);
    expect(await screen.findByTestId("change-freeze-tab")).toBeInTheDocument();
  });

  it("lists the templates with type, pre-approval and task count", async () => {
    serveChangeAdmin();
    await renderChangeAdmin(ChangeAdminPage, ROUTE);
    const rows = await screen.findAllByTestId("template-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Backup-Zeitplan ändern");
    expect(rows[0]).toHaveTextContent("Standard-Change");
    expect(within(rows[0]).getByTestId("template-standard")).toHaveTextContent("Vorab freigegeben");
    expect(rows[0]).toHaveTextContent("3 Aufgaben");
    expect(rows[1]).toHaveTextContent("Normaler Change");
    expect(within(rows[1]).queryByTestId("template-standard")).toBeNull();
    expect(within(rows[0]).getByTestId("template-edit")).toHaveAttribute("href", "/admin/itsm/change/templates/1");
    expect(screen.getByTestId("template-new")).toHaveAttribute("href", "/admin/itsm/change/templates/new");
  });

  it("deletes a template after asking", async () => {
    const requests: Recorded[] = [];
    serveChangeAdmin(requests);
    await renderChangeAdmin(ChangeAdminPage, ROUTE);
    fireEvent.click(within((await screen.findAllByTestId("template-row"))[1]).getByTestId("template-delete"));
    expect(screen.getByTestId("confirm-dialog")).toHaveTextContent("Vorlage „Server-Wartung“ löschen?");
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(requests.some((r) => r.method === "DELETE" && r.path === `${BASE}/templates/2`)).toBe(true));
  });

  it("marks a broken template", async () => {
    serveChangeAdmin([], (method, path) => {
      if (method !== "GET" || path !== `${BASE}/templates`) return undefined;
      return [
        {
          id: 3, name: "Alte Vorlage", description: null, change_type: "normal", is_standard: false, valid: false, broken: true,
          defaults: { portal_visible: false }, tasks: [], created_at: "2026-10-12T08:00:00Z", created_by: 1, updated_at: "2026-10-12T08:00:00Z", updated_by: 1,
        },
      ];
    });
    await renderChangeAdmin(ChangeAdminPage, ROUTE);
    const row = await screen.findByTestId("template-row");
    expect(within(row).getByTestId("template-broken")).toHaveTextContent("Fehlerhaft");
    expect(within(row).getByTestId("template-invalid")).toHaveTextContent("Ungültig");
    expect(row).toHaveTextContent("Die Vorlage verweist auf etwas, das es nicht mehr gibt.");
  });

  it("lists the freeze windows with effect, scope, recurrence and UTC times", async () => {
    serveChangeAdmin();
    await renderChangeAdmin(ChangeAdminPage, ROUTE, `${ROUTE}?tab=freeze`);
    const rows = await screen.findAllByTestId("freeze-row");
    expect(rows[0]).toHaveTextContent("Quartalsabschluss");
    expect(rows[0]).toHaveTextContent("Changes sperren");
    expect(rows[0]).toHaveTextContent("2026-10-13 00:00 – 2026-10-16 00:00");
    expect(rows[0]).toHaveTextContent("Monatlich, Intervall 3");
    await waitFor(() => expect(rows[0]).toHaveTextContent("Kundenportal · Monatlich, Intervall 3"));
    expect(rows[1]).toHaveTextContent("Nur warnen");
    expect(rows[1]).toHaveTextContent("alle Services");
    expect(screen.getByTestId("change-freeze-tab")).toHaveTextContent("Zeiten in UTC");
    fireEvent.click(within(rows[0]).getByTestId("freeze-edit"));
    expect(await screen.findByTestId("freeze-dialog")).toBeInTheDocument();
  });

  it("explains a load failure with the change error text", async () => {
    serveChangeAdmin([], (_method, path) => {
      if (path === `${BASE}/templates`) throw new ApiError(404, { detail: { code: "change_unavailable" } }, path);
      return undefined;
    });
    await renderChangeAdmin(ChangeAdminPage, ROUTE);
    expect(await screen.findByTestId("templates-error")).toHaveTextContent("Change-Management ist nicht eingeschaltet.");
  });
});
