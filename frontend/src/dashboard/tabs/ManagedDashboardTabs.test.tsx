import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { DashboardConflictError, dashboardKeys, dashboardsApi, type Dashboard } from "@/lib/dashboardsApi";
import { demoDashboard, demoDefaultWidgets } from "@/demo/dashboardsMock";
import { makeUser } from "@/test/fireUser";
import { ManagedDashboardTabs } from "./ManagedDashboardTabs";

const user = makeUser();
const WIDGETS = demoDefaultWidgets();

let server: Dashboard[];
const seed = (): Dashboard[] => [
  demoDashboard({ id: 1, name: "Mein Start", version: 3, sort: 0, widgets: WIDGETS }),
  demoDashboard({ id: 4, name: "Eigen B", sort: 1 }),
  demoDashboard({ id: 2, name: "Team Support", kind: "shared", is_owner: false, can_edit: false, owner_name: "Maya Rivera", sort: 2 }),
  demoDashboard({ id: 3, name: "Leitstand", kind: "template", locked: true, is_owner: false, can_edit: false, sort: 3 }),
];
const tabOf = ({ widgets: _w, ...tab }: Dashboard) => tab;

const spies = {
  list: vi.spyOn(dashboardsApi, "list"),
  get: vi.spyOn(dashboardsApi, "get"),
  create: vi.spyOn(dashboardsApi, "create"),
  save: vi.spyOn(dashboardsApi, "save"),
  remove: vi.spyOn(dashboardsApi, "remove"),
  order: vi.spyOn(dashboardsApi, "order"),
};

function Harness() {
  const q = useQuery({ queryKey: dashboardKeys.list(), queryFn: () => dashboardsApi.list() });
  const [selected, setSelected] = useState<number | null>(1);
  if (!q.data) return null;
  return <ManagedDashboardTabs tabs={q.data} selectedId={selected} onSelect={setSelected} />;
}

async function renderTabs() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Harness />
    </QueryClientProvider>,
  );
  await screen.findByRole("tab", { name: "Mein Start" });
}

const menuButtonOf = (name: string) => screen.getByRole("button", { name: `Menü für ${name}` });
const menuOf = (name: string) => menuButtonOf(name);
const openNew = async () => user.click(screen.getByRole("button", { name: "Neues Dashboard" }));

beforeAll(async () => {
  await i18n.changeLanguage("de");
});

beforeEach(() => {
  server = seed();
  spies.list.mockImplementation(async () => [...server].sort((a, b) => a.sort - b.sort).map(tabOf));
  spies.get.mockImplementation(async (id) => server.find((d) => d.id === id)!);
  spies.create.mockImplementation(async (body) => {
    const d = demoDashboard({ id: 50 + server.length, name: body.name, sort: server.length });
    server = [...server, d];
    return d;
  });
  spies.save.mockImplementation(async (id, body) => {
    const d = { ...server.find((x) => x.id === id)!, name: body.name, version: body.version + 1 };
    server = server.map((x) => (x.id === id ? d : x));
    return d;
  });
  spies.remove.mockImplementation(async (id) => {
    server = server.filter((d) => d.id !== id);
  });
  spies.order.mockImplementation(async (ids) => {
    server = server.map((d) => (ids.includes(d.id) ? { ...d, sort: ids.indexOf(d.id) } : d));
  });
});

afterEach(() => {
  cleanup();
  for (const spy of Object.values(spies)) spy.mockReset();
});

describe("dashboard management in the tab row", () => {
  it("'+' opens the dialog; the name is required and trimmed; creating selects the new tab", async () => {
    await renderTabs();
    await openNew();
    await user.click(screen.getByRole("button", { name: "Anlegen" }));
    expect(screen.getByText("Bitte einen Namen eingeben.")).toBeVisible();
    expect(spies.create).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText("Name"), "  Mein Tag  ");
    await user.click(screen.getByRole("button", { name: "Anlegen" }));
    expect(spies.create).toHaveBeenCalledWith({ name: "Mein Tag", icon: null, copy_from: null });
    expect(await screen.findByRole("tab", { name: "Mein Tag", selected: true })).toBeVisible();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("'Kopie von' passes copy_from", async () => {
    await renderTabs();
    await openNew();
    await user.type(screen.getByLabelText("Name"), "Kopie");
    await user.selectOptions(screen.getByLabelText("Kopie von"), "Team Support");
    await user.click(screen.getByRole("button", { name: "Anlegen" }));
    expect(spies.create).toHaveBeenCalledWith({ name: "Kopie", icon: null, copy_from: 2 });
  });

  it("the 21st dashboard shows the server's message and keeps the dialog open", async () => {
    spies.create.mockRejectedValue(
      new ApiError(422, { detail: { message: "Es sind höchstens 20 Dashboards möglich.", path: "name" } }, "/api/v1/dashboards"),
    );
    await renderTabs();
    await openNew();
    await user.type(screen.getByLabelText("Name"), "Zu viel");
    await user.click(screen.getByRole("button", { name: "Anlegen" }));
    expect(await screen.findByText("Es sind höchstens 20 Dashboards möglich.")).toBeVisible();
    expect(screen.getByRole("dialog")).toBeVisible();
    expect(screen.getByLabelText("Name")).toHaveValue("Zu viel");
  });

  it("rename saves with the current widgets and the new name; a 409 keeps the old name and says why", async () => {
    await renderTabs();
    await user.click(menuOf("Mein Start"));
    await user.click(screen.getByRole("menuitem", { name: "Umbenennen" }));
    await user.clear(screen.getByLabelText("Name"));
    await user.type(screen.getByLabelText("Name"), "Mein Tag");
    await user.click(screen.getByRole("button", { name: "Speichern" }));
    await waitFor(() =>
      expect(spies.save).toHaveBeenCalledWith(1, expect.objectContaining({ name: "Mein Tag", version: 3, widgets: WIDGETS })),
    );
    expect(await screen.findByRole("tab", { name: "Mein Tag" })).toBeVisible();

    spies.save.mockRejectedValue(new DashboardConflictError("Dieses Dashboard wurde inzwischen geändert.", 4));
    await user.click(menuOf("Mein Tag"));
    await user.click(screen.getByRole("menuitem", { name: "Umbenennen" }));
    await user.click(screen.getByRole("button", { name: "Speichern" }));
    expect(await screen.findByText(/inzwischen geändert/)).toBeVisible();
    expect(screen.getByRole("tab", { name: "Mein Tag" })).toBeVisible();
  });

  it("move left/right calls order with the permuted own ids; disabled at the ends and absent for foreign tabs", async () => {
    await renderTabs();
    await user.click(menuOf("Eigen B"));
    await user.click(screen.getByRole("menuitem", { name: "Nach links" }));
    expect(spies.order).toHaveBeenCalledWith([4, 1]);
    await waitFor(() =>
      expect(screen.getAllByRole("tab").map((tab) => tab.querySelector("span.truncate")?.textContent)).toEqual([
        "Eigen B",
        "Mein Start",
        "Team Support",
        "Leitstand",
      ]),
    );

    await user.click(menuOf("Eigen B")); // now the first tab
    expect(screen.getByRole("menuitem", { name: "Nach links" })).toBeDisabled();
    expect(screen.getByRole("menuitem", { name: "Nach rechts" })).toBeEnabled();
    await user.keyboard("{Escape}");
    await user.click(menuOf("Team Support")); // shared by someone else
    expect(screen.queryByRole("menuitem", { name: "Nach links" })).toBeNull();
  });

  it("delete asks for confirmation, then removes and selects the neighbour; the last own dashboard cannot be deleted", async () => {
    await renderTabs();
    await user.click(screen.getByRole("tab", { name: "Eigen B" }));
    await user.click(menuOf("Eigen B"));
    await user.click(screen.getByRole("menuitem", { name: "Löschen" }));
    const dialog = screen.getByRole("alertdialog");
    expect(within(dialog).getByText("Dashboard „Eigen B“ löschen?")).toBeVisible();
    expect(spies.remove).not.toHaveBeenCalled();
    await user.click(within(dialog).getByRole("button", { name: "Löschen" }));
    expect(spies.remove).toHaveBeenCalledWith(4);
    expect(await screen.findByRole("tab", { name: "Mein Start", selected: true })).toBeVisible();
    expect(screen.queryByRole("tab", { name: "Eigen B" })).toBeNull();

    await user.click(menuOf("Mein Start"));
    const del = screen.getByRole("menuitem", { name: "Löschen" });
    expect(del).toHaveAttribute("aria-disabled", "true");
    expect(del).toHaveAttribute("title", "Das letzte Dashboard bleibt bestehen.");
    await user.click(del);
    expect(screen.queryByRole("alertdialog")).toBeNull();
  });

  it("a failed delete says why and keeps the tab", async () => {
    spies.remove.mockRejectedValue(
      new ApiError(422, { detail: { message: "Das letzte Dashboard bleibt bestehen.", path: "id" } }, "/api/v1/dashboards/4"),
    );
    await renderTabs();
    await user.click(menuOf("Eigen B"));
    await user.click(screen.getByRole("menuitem", { name: "Löschen" }));
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Löschen" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Das letzte Dashboard bleibt bestehen.");
    expect(screen.getByRole("tab", { name: "Eigen B" })).toBeVisible();
  });

  it("a shared or locked tab offers copy and no rename/delete", async () => {
    await renderTabs();
    await user.click(menuOf("Team Support"));
    expect(screen.queryByRole("menuitem", { name: "Umbenennen" })).toBeNull();
    expect(screen.queryByRole("menuitem", { name: "Löschen" })).toBeNull();
    await user.click(screen.getByRole("menuitem", { name: "Als eigenes Dashboard kopieren" }));
    expect(spies.create).toHaveBeenCalledWith({ name: "Team Support (Kopie)", icon: null, copy_from: 2 });
    expect(await screen.findByRole("tab", { name: "Team Support (Kopie)", selected: true })).toBeVisible();

    await user.click(menuOf("Leitstand"));
    expect(screen.getAllByRole("menuitem").map((i) => i.textContent)).toEqual(["Als eigenes Dashboard kopieren"]);
  });

  it("the tab menu is keyboard operable", async () => {
    await renderTabs();
    await user.click(screen.getByRole("tab", { name: "Eigen B" }));
    menuButtonOf("Eigen B").focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("menu")).toBeVisible();
    const items = screen.getAllByRole("menuitem");
    expect(items[0]).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(items[1]).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(menuButtonOf("Eigen B")).toHaveFocus();
  });
});
