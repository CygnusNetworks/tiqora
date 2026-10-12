import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { RouterProvider, createMemoryHistory, createRootRoute, createRoute, createRouter } from "@tanstack/react-router";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { dashboardsApi, type KioskLinkCreated } from "@/lib/dashboardsApi";
import { demoDashboard, demoTab } from "@/demo/dashboardsMock";
import { makeUser } from "@/test/fireUser";
import { KioskLinksDialog } from "./KioskLinksDialog";

const user = makeUser();
const TABS = [demoTab({ id: 1, name: "Mein Start" }), demoTab({ id: 2, name: "Leitstand" })];

const CREATED: KioskLinkCreated = {
  id: 5,
  name: "Lobby",
  dashboards: [{ id: 2, name: "Leitstand", visible: true }],
  rotate_seconds: 60,
  show_details: false,
  created_at: "2026-10-12T08:00:00Z",
  created_by: 1,
  created_by_name: "Alex Turner",
  expires_at: "2027-01-10T08:00:00Z",
  last_used_at: null,
  revoked_at: null,
  active: true,
  token_hint: "…abcd",
  token: "tok/with space",
};

const spies = {
  create: vi.spyOn(dashboardsApi.kioskLinks, "create"),
  get: vi.spyOn(dashboardsApi, "get"),
};

async function renderDialog(onClose = vi.fn()) {
  const rootRoute = createRootRoute();
  const page = createRoute({
    getParentRoute: () => rootRoute,
    path: "/",
    component: () => <KioskLinksDialog dashboards={TABS} initialDashboardId={2} onClose={onClose} />,
  });
  const admin = createRoute({ getParentRoute: () => rootRoute, path: "/admin/dashboard-kiosk-links" });
  const router = createRouter({ routeTree: rootRoute.addChildren([page, admin]), history: createMemoryHistory({ initialEntries: ["/"] }) });
  await act(async () => {
    await router.load();
  });
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <I18nextProvider i18n={i18n}>
        {/* eslint-disable-next-line @typescript-eslint/no-explicit-any */}
        <RouterProvider router={router as any} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  await screen.findByRole("dialog");
  return onClose;
}

async function createLink() {
  await user.type(await screen.findByLabelText("Name"), "Lobby");
  await user.click(screen.getByRole("button", { name: "Link erstellen" }));
}

beforeAll(async () => {
  window.scrollTo = () => {};
  await i18n.changeLanguage("de");
});

beforeEach(() => {
  spies.create.mockResolvedValue(CREATED);
  spies.get.mockResolvedValue(demoDashboard({ id: 2, name: "Leitstand" }));
});

afterEach(() => {
  cleanup();
  for (const spy of Object.values(spies)) spy.mockReset();
  Reflect.deleteProperty(navigator, "clipboard");
  Reflect.deleteProperty(document, "execCommand");
});

describe("KioskLinksDialog", () => {
  it("creates the link and shows the address once, with the one-time and rights notes", async () => {
    await renderDialog();
    await createLink();
    await waitFor(() =>
      expect(spies.create).toHaveBeenCalledWith({ name: "Lobby", dashboard_ids: [2], rotate_seconds: 60, expires_in_days: 90, show_details: false }),
    );
    const field = await screen.findByLabelText("Wallboard-Link");
    expect(field).toHaveAttribute("readonly");
    expect(field).toHaveValue(`${window.location.origin}/wallboard#tok%2Fwith%20space`);
    expect(screen.getByText("Dieser Link wird nur jetzt angezeigt.")).toBeVisible();
    expect(screen.getByText(/Wer den Link hat, sieht das Wallboard ohne Anmeldung/)).toBeVisible();
  });

  it("copies with the clipboard API", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    await renderDialog();
    await createLink();
    await user.click(await screen.findByRole("button", { name: "Kopieren" }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(`${window.location.origin}/wallboard#tok%2Fwith%20space`));
    expect(await screen.findByRole("button", { name: "Kopiert" })).toBeVisible();
  });

  it("without a clipboard API (http page) selects the read-only field for a manual copy", async () => {
    Object.defineProperty(document, "execCommand", { value: vi.fn(() => false), configurable: true });
    await renderDialog();
    await createLink();
    const field = (await screen.findByLabelText("Wallboard-Link")) as HTMLInputElement;
    await user.click(screen.getByRole("button", { name: "Kopieren" }));
    expect(await screen.findByText(/Kopieren war nicht möglich/)).toBeVisible();
    expect(field.selectionStart).toBe(0);
    expect(field.selectionEnd).toBe(field.value.length);
  });

  it("shows the server's refusal in the form and keeps it open", async () => {
    spies.create.mockRejectedValue(new ApiError(422, { detail: { message: "Jedes Dashboard darf nur einmal vorkommen." } }, "/api/v1/admin/dashboard-kiosk-links"));
    await renderDialog();
    await createLink();
    expect(await screen.findByRole("alert")).toHaveTextContent(/Jedes Dashboard darf nur einmal vorkommen|konnte nicht erstellt werden/);
    expect(screen.getByLabelText("Name")).toBeVisible();
  });
});
