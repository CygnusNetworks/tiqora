import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import i18n from "@/i18n";
import { dashboardsApi } from "@/lib/dashboardsApi";
import { demoDashboard, demoDefaultWidgets, demoTab } from "@/demo/dashboardsMock";
import { makeUser } from "@/test/fireUser";
import { KioskLinkForm } from "./KioskLinkForm";
import { dashboardRows, hasMineWidget } from "./kioskLinks";

const user = makeUser();

const TABS = [demoTab({ id: 1, name: "Mein Start" }), demoTab({ id: 2, name: "Leitstand" }), demoTab({ id: 3, name: "Eingang" })];
const TALL = demoDashboard({ id: 1, name: "Mein Start", widgets: demoDefaultWidgets() });
const FLAT = demoDashboard({
  id: 2,
  name: "Leitstand",
  widgets: demoDefaultWidgets().filter((w) => w.type_key === "core.queue-overview" || w.type_key === "core.recent-views"),
});

const get = vi.spyOn(dashboardsApi, "get");

function renderForm(over: { initial?: number | null; onSubmit?: (b: unknown) => void } = {}) {
  const onSubmit = over.onSubmit ?? vi.fn();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <KioskLinkForm
        dashboards={TABS}
        initialDashboardId={over.initial === undefined ? 1 : over.initial}
        pending={false}
        error={null}
        onSubmit={onSubmit}
        onCancel={() => {}}
      />
    </QueryClientProvider>,
  );
  return onSubmit;
}

beforeAll(async () => {
  await i18n.changeLanguage("de");
});

afterEach(() => {
  cleanup();
  get.mockReset();
});

describe("kioskLinks helpers", () => {
  it("counts rows on the large layout and finds mine presets in counters and lists only", () => {
    expect(dashboardRows(TALL)).toBe(20);
    expect(dashboardRows(demoDashboard())).toBe(0);
    expect(hasMineWidget(TALL)).toBe(true);
    expect(hasMineWidget(FLAT)).toBe(false);
  });
});

describe("KioskLinkForm", () => {
  it("starts with the current dashboard, 60 s rotation, 90 days and details off", async () => {
    get.mockResolvedValue(FLAT);
    renderForm({ initial: 2 });
    expect(screen.getByRole("checkbox", { name: "Leitstand" })).toBeChecked();
    expect(screen.getByRole("checkbox", { name: "Mein Start" })).not.toBeChecked();
    expect(screen.getByLabelText("Wechsel alle")).toHaveValue(60);
    expect(screen.getByLabelText("Gültig für")).toHaveValue(90);
    expect(screen.getByRole("switch", { name: /Details anzeigen/ })).not.toBeChecked();
    expect(screen.getByText(/Wer den Link hat, sieht das Wallboard ohne Anmeldung/)).toBeVisible();
    await waitFor(() => expect(get).toHaveBeenCalled());
  });

  it("requires a name and a dashboard, and keeps rotation and expiry within the server bounds", async () => {
    const onSubmit = renderForm({ initial: null });
    await user.click(screen.getByRole("button", { name: "Link erstellen" }));
    expect(screen.getByText("Bitte einen Namen eingeben.")).toBeVisible();
    expect(screen.getByText("Bitte mindestens ein Dashboard wählen.")).toBeVisible();
    expect(onSubmit).not.toHaveBeenCalled();

    await user.type(screen.getByLabelText("Name"), "Lobby");
    get.mockResolvedValue(FLAT);
    await user.click(screen.getByRole("checkbox", { name: "Leitstand" }));
    for (const [label, bad] of [
      ["Wechsel alle", "14"],
      ["Wechsel alle", "901"],
      ["Gültig für", "0"],
      ["Gültig für", "366"],
    ] as const) {
      await user.clear(screen.getByLabelText(label));
      await user.type(screen.getByLabelText(label), bad);
      await user.click(screen.getByRole("button", { name: "Link erstellen" }));
      expect(onSubmit, `${label}=${bad}`).not.toHaveBeenCalled();
      await user.clear(screen.getByLabelText(label));
      await user.type(screen.getByLabelText(label), label === "Wechsel alle" ? "60" : "90");
    }
    expect(screen.queryByText("Zwischen 15 und 900 Sekunden.")).toBeNull();
  });

  it("submits exactly the chosen values", async () => {
    get.mockResolvedValue(FLAT);
    const onSubmit = renderForm({ initial: 2 });
    await user.type(screen.getByLabelText("Name"), "  Lobby  ");
    await user.click(screen.getByRole("checkbox", { name: "Eingang" }));
    await user.clear(screen.getByLabelText("Wechsel alle"));
    await user.type(screen.getByLabelText("Wechsel alle"), "30");
    await user.clear(screen.getByLabelText("Gültig für"));
    await user.type(screen.getByLabelText("Gültig für"), "7");
    await user.click(screen.getByRole("switch", { name: /Details anzeigen/ }));
    await user.click(screen.getByRole("button", { name: "Link erstellen" }));
    expect(onSubmit).toHaveBeenCalledWith({
      name: "Lobby",
      dashboard_ids: [2, 3],
      rotate_seconds: 30,
      expires_in_days: 7,
      show_details: true,
    });
  });

  it("warns about a dashboard taller than 14 rows and about mine tiles, but not for a small one", async () => {
    get.mockImplementation(async (id) => (id === 1 ? TALL : FLAT));
    renderForm({ initial: 2 });
    await waitFor(() => expect(get).toHaveBeenCalledWith(2, expect.anything()));
    expect(screen.queryByText(/zu hoch für ein Wallboard/)).toBeNull();
    expect(screen.queryByText(/Zeigt die Tickets der Person, die den Link erstellt/)).toBeNull();
    await user.click(screen.getByRole("checkbox", { name: "Mein Start" }));
    expect(await screen.findByText(/„Mein Start“ ist zu hoch für ein Wallboard \(mehr als 14 Zeilen\)/)).toBeVisible();
    expect(screen.getByText(/„Mein Start“ enthält Kacheln mit „Meine Tickets“\. Zeigt die Tickets der Person, die den Link erstellt/)).toBeVisible();
  });
});
