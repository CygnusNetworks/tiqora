import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import i18n from "@/i18n";
import { dashboardKeys, dashboardsApi } from "@/lib/dashboardsApi";
import { demoDashboard } from "@/demo/dashboardsMock";
import { makeUser } from "@/test/fireUser";
import { ManagedDashboardTabs } from "./ManagedDashboardTabs";

const user = makeUser();
let auth: { user: { is_admin: boolean } | null } | null = null;

vi.mock("@/auth/AuthContext", () => ({ useOptionalAuth: () => auth }));
vi.mock("@tanstack/react-router", async (orig) => ({
  ...(await orig<typeof import("@tanstack/react-router")>()),
  Link: ({ children }: { children: React.ReactNode }) => <a href="/x">{children}</a>,
}));

const D1 = demoDashboard({ id: 1, name: "Mein Start" });
const D2 = demoDashboard({ id: 2, name: "Leitstand", sort: 1 });
const tabOf = ({ widgets: _w, ...tab }: typeof D1) => tab;

function Harness() {
  const q = useQuery({ queryKey: dashboardKeys.list(), queryFn: () => dashboardsApi.list() });
  const [selected, setSelected] = useState<number | null>(2);
  if (!q.data) return null;
  return <ManagedDashboardTabs tabs={q.data} selectedId={selected} onSelect={setSelected} />;
}

async function openMenu() {
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <Harness />
    </QueryClientProvider>,
  );
  await screen.findByRole("tab", { name: "Leitstand" });
  await user.click(screen.getByRole("button", { name: "Menü für Leitstand" }));
}

beforeAll(async () => {
  await i18n.changeLanguage("de");
});

beforeEach(() => {
  vi.spyOn(dashboardsApi, "list").mockResolvedValue([tabOf(D1), tabOf(D2)]);
  vi.spyOn(dashboardsApi, "get").mockImplementation(async (id) => (id === 1 ? D1 : D2));
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("Wallboard-Link erstellen … in the tab menu", () => {
  it("is offered to admins and opens the dialog with the selected dashboard preselected", async () => {
    auth = { user: { is_admin: true } };
    await openMenu();
    await user.click(screen.getByRole("menuitem", { name: "Wallboard-Link erstellen …" }));
    expect(await screen.findByRole("dialog", { name: "Wallboard-Link erstellen" })).toBeVisible();
    expect(screen.getByRole("checkbox", { name: "Leitstand" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Mein Start" })).not.toBeChecked();
  });

  it("is absent for non-admins", async () => {
    auth = { user: { is_admin: false } };
    await openMenu();
    expect(screen.queryByRole("menuitem", { name: /Wallboard-Link erstellen/ })).toBeNull();
  });

  it("is absent without a session", async () => {
    auth = null;
    await openMenu();
    expect(screen.queryByRole("menuitem", { name: /Wallboard-Link erstellen/ })).toBeNull();
  });
});
