import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import { DEMO_FACTS } from "@/demo/reportsMock";
import i18n from "@/i18n";
import { reportsApi } from "@/lib/reportsApi";
import { FactsBanner } from "./FactsBanner";

vi.mock("@/auth/AuthContext", () => ({ useAuth: () => ({ user: { is_admin: false } }) }));

const UPGRADING =
  "Die Kennzahlen werden nach dem Update neu berechnet. First-Level- und ITSM-Lösungszeit-Werte sind bis dahin unvollständig.";

function mount(facts: typeof DEMO_FACTS) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={i18n}>
        <FactsBanner facts={facts} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return { client, invalidate };
}

beforeAll(async () => {
  await i18n.changeLanguage("de");
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("FactsBanner", () => {
  it("appears while the fact upgrade has rows left, also from a result that does not count", async () => {
    vi.spyOn(reportsApi, "factsStatus").mockResolvedValue({ ...DEMO_FACTS, outdated_rows: 500 });
    mount({ ...DEMO_FACTS, outdated_rows: null });
    expect(await screen.findByTestId("reports-facts-banner")).toHaveTextContent(UPGRADING);
  });

  it("stays away when nothing is outdated", async () => {
    const status = vi.spyOn(reportsApi, "factsStatus").mockResolvedValue(DEMO_FACTS);
    mount({ ...DEMO_FACTS, outdated_rows: null });
    await waitFor(() => expect(status).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("reports-facts-banner")).not.toBeInTheDocument();
  });

  it("goes away and reloads the reports when the upgrade is done", async () => {
    vi.spyOn(reportsApi, "factsStatus").mockResolvedValue({ ...DEMO_FACTS, outdated_rows: 1 });
    const { client, invalidate } = mount({ ...DEMO_FACTS, outdated_rows: 1 });
    await screen.findByTestId("reports-facts-banner");
    vi.spyOn(reportsApi, "factsStatus").mockResolvedValue(DEMO_FACTS);
    void client.refetchQueries({ queryKey: ["reports", "facts"] });
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ["reports"] }));
  });

  it("the build notice wins over the upgrade notice", async () => {
    const building = { ...DEMO_FACTS, ready: false, backfill_status: "running" as const, backfill_progress: 0.42, outdated_rows: 5 };
    vi.spyOn(reportsApi, "factsStatus").mockResolvedValue(building);
    mount(building);
    await waitFor(() => expect(screen.getByTestId("reports-facts-banner")).toHaveTextContent("42"));
    expect(screen.getByTestId("reports-facts-banner")).not.toHaveTextContent("nach dem Update");
  });
});
