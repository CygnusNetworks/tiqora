import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { dashboardsApi, type KioskLink } from "@/lib/dashboardsApi";
import { makeUser } from "@/test/fireUser";
import { sortLinks } from "@/dashboard/kiosk/kioskLinks";
import { DashboardKioskLinksPage } from "./DashboardKioskLinksPage";

const user = makeUser();

const link = (over: Partial<KioskLink> & { id: number; name: string }): KioskLink => ({
  dashboards: [{ id: 1, name: "Leitstand", visible: true }],
  rotate_seconds: 60,
  show_details: false,
  created_at: "2026-09-01T08:00:00Z",
  created_by: 1,
  created_by_name: "Alex Turner",
  expires_at: "2099-01-01T08:00:00Z",
  last_used_at: null,
  revoked_at: null,
  active: true,
  token_hint: "…abcd",
  ...over,
});

const OLD_ACTIVE = link({ id: 1, name: "Flur", created_at: "2026-08-01T08:00:00Z", last_used_at: "2026-10-11T08:00:00Z" });
const REVOKED = link({ id: 2, name: "Lobby alt", active: false, revoked_at: "2026-10-01T08:00:00Z", created_at: "2026-10-02T08:00:00Z" });
const NEW_ACTIVE = link({ id: 3, name: "Lobby", show_details: true, created_at: "2026-10-05T08:00:00Z", dashboards: [{ id: 9, name: "Weg", visible: false }] });
const EXPIRED = link({ id: 4, name: "Abgelaufen", active: false, expires_at: "2026-09-30T08:00:00Z", created_at: "2026-09-01T08:00:00Z" });

const spies = {
  list: vi.spyOn(dashboardsApi.kioskLinks, "list"),
  revoke: vi.spyOn(dashboardsApi.kioskLinks, "revoke"),
  remove: vi.spyOn(dashboardsApi.kioskLinks, "remove"),
  tabs: vi.spyOn(dashboardsApi, "list"),
};

function renderPage() {
  return render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nextProvider i18n={i18n}>
        <DashboardKioskLinksPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

/** The row's name cell starts with the link name as its own text node (creator and flags follow in a div). */
const names = () => screen.getAllByRole("rowheader").map((h) => h.firstChild?.textContent ?? "");
const rowOf = (name: string): HTMLElement => {
  const head = screen.getAllByRole("rowheader").find((h) => h.firstChild?.textContent === name);
  if (!head) throw new Error(`no row for ${name}`);
  return head.closest("tr") as HTMLElement;
};
const rowReady = (name: string) => waitFor(() => rowOf(name));

beforeAll(async () => {
  window.scrollTo = () => {};
  await i18n.changeLanguage("de");
});

beforeEach(() => {
  spies.list.mockResolvedValue([OLD_ACTIVE, REVOKED, NEW_ACTIVE, EXPIRED]);
  spies.revoke.mockResolvedValue({ ...OLD_ACTIVE, active: false, revoked_at: "2026-10-12T08:00:00Z" });
  spies.remove.mockResolvedValue(undefined);
  spies.tabs.mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  for (const spy of Object.values(spies)) spy.mockReset();
});

describe("DashboardKioskLinksPage", () => {
  it("sorts active links first, newest first", () => {
    expect(sortLinks([OLD_ACTIVE, REVOKED, NEW_ACTIVE, EXPIRED]).map((l) => l.id)).toEqual([3, 1, 2, 4]);
  });

  it("lists active links first with state, usage and the creator", async () => {
    renderPage();
    await rowReady("Lobby");
    expect(names()).toEqual(["Lobby", "Flur", "Lobby alt", "Abgelaufen"]);
    const lobby = rowOf("Lobby");
    expect(within(lobby).getByText("Aktiv")).toBeVisible();
    expect(within(lobby).getByText(/mit Details/)).toBeVisible();
    expect(within(lobby).getByText(/Weg/)).toBeVisible();
    expect(within(lobby).getByText(/nicht mehr sichtbar/)).toBeVisible();
    expect(within(lobby).getByText("noch nie verwendet")).toBeVisible();
    const flur = rowOf("Flur");
    expect(within(flur).getByText(/zuletzt verwendet/)).toBeVisible();
    expect(within(rowOf("Lobby alt")).getByText("Widerrufen", { selector: "span" })).toBeVisible();
    expect(within(rowOf("Abgelaufen")).getByText("Abgelaufen", { selector: "span" })).toBeVisible();
  });

  it("offers Widerrufen on active links and Löschen only on inactive ones", async () => {
    renderPage();
    await rowReady("Flur");
    expect(within(rowOf("Flur")).getByRole("button", { name: /Widerrufen/ })).toBeVisible();
    expect(within(rowOf("Flur")).queryByRole("button", { name: /Löschen/ })).toBeNull();
    const revoked = rowOf("Lobby alt");
    expect(within(revoked).getByRole("button", { name: /Löschen/ })).toBeVisible();
    expect(within(revoked).queryByRole("button", { name: /Widerrufen/ })).toBeNull();
  });

  it("revokes only after confirmation", async () => {
    renderPage();
    const row = await rowReady("Flur");
    await user.click(within(row).getByRole("button", { name: /Widerrufen/ }));
    const dialog = await screen.findByRole("alertdialog");
    expect(spies.revoke).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "Widerrufen" }));
    await waitFor(() => expect(spies.revoke).toHaveBeenCalledWith(1));
    await waitFor(() => expect(screen.queryByRole("alertdialog")).toBeNull());
  });

  it("deletes only after confirmation, and cancelling deletes nothing", async () => {
    renderPage();
    const row = await rowReady("Lobby alt");
    await user.click(within(row).getByRole("button", { name: /Löschen/ }));
    let dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Abbrechen" }));
    expect(spies.remove).not.toHaveBeenCalled();
    await user.click(within(rowOf("Lobby alt")).getByRole("button", { name: /Löschen/ }));
    dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Löschen" }));
    await waitFor(() => expect(spies.remove).toHaveBeenCalledWith(2));
  });

  it("shows a failed action instead of closing silently", async () => {
    spies.revoke.mockRejectedValue(new ApiError(500, { detail: "x" }, "/api/v1/admin/dashboard-kiosk-links/1/revoke"));
    renderPage();
    const row = await rowReady("Flur");
    await user.click(within(row).getByRole("button", { name: /Widerrufen/ }));
    const dialog = await screen.findByRole("alertdialog");
    await user.click(within(dialog).getByRole("button", { name: "Widerrufen" }));
    expect(await screen.findByRole("alert")).toBeVisible();
  });

  it("explains the empty state", async () => {
    spies.list.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText(/Es gibt noch keine Wallboard-Links/)).toBeVisible();
  });
});
