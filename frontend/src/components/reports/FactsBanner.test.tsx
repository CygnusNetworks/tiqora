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

function mount(facts: typeof DEMO_FACTS | undefined) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const tree = (f: typeof facts) => (
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={i18n}>
        <FactsBanner facts={f} />
      </I18nextProvider>
    </QueryClientProvider>
  );
  const view = render(tree(facts));
  return { client, invalidate, rerender: (f: typeof facts) => view.rerender(tree(f)) };
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
    vi.spyOn(reportsApi, "factsStatus").mockResolvedValue({ ...DEMO_FACTS, outdated_rows: 12345 });
    mount({ ...DEMO_FACTS, outdated_rows: null });
    expect(await screen.findByTestId("reports-facts-banner")).toHaveTextContent(UPGRADING);
  });

  it("stays through a report re-run, which has no status for a moment", async () => {
    vi.spyOn(reportsApi, "factsStatus").mockResolvedValue({ ...DEMO_FACTS, outdated_rows: 501 });
    const { invalidate, rerender } = mount({ ...DEMO_FACTS, outdated_rows: null });
    const banner = await screen.findByTestId("reports-facts-banner");
    rerender(undefined);
    expect(screen.getByTestId("reports-facts-banner")).toBe(banner);
    rerender({ ...DEMO_FACTS, outdated_rows: null });
    expect(screen.getByTestId("reports-facts-banner")).toBe(banner);
    expect(invalidate).not.toHaveBeenCalled();
  });

  it("a remainder of at most one batch shows no notice and is not polled", async () => {
    const status = vi.spyOn(reportsApi, "factsStatus").mockResolvedValue({ ...DEMO_FACTS, outdated_rows: 500 });
    const { client } = mount({ ...DEMO_FACTS, outdated_rows: 500 });
    await waitFor(() => expect(status).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("reports-facts-banner")).not.toBeInTheDocument();
    const query = client.getQueryCache().find({ queryKey: ["reports", "facts"] })!;
    const observer = query.observers[0];
    expect(observer.options.staleTime).toBeGreaterThan(0);
    const interval = observer.options.refetchInterval as (q: typeof query) => number | false;
    expect(interval(query)).toBe(false);
  });

  it("501 rows show the notice and keep the slow poll", async () => {
    vi.spyOn(reportsApi, "factsStatus").mockResolvedValue({ ...DEMO_FACTS, outdated_rows: 501 });
    const { client } = mount({ ...DEMO_FACTS, outdated_rows: 501 });
    expect(await screen.findByTestId("reports-facts-banner")).toHaveTextContent(UPGRADING);
    const query = client.getQueryCache().find({ queryKey: ["reports", "facts"] })!;
    const interval = query.observers[0].options.refetchInterval as (q: typeof query) => number | false;
    expect(interval(query)).toBe(5 * 60_000);
  });

  it("stays away when nothing is outdated", async () => {
    const status = vi.spyOn(reportsApi, "factsStatus").mockResolvedValue(DEMO_FACTS);
    mount({ ...DEMO_FACTS, outdated_rows: null });
    await waitFor(() => expect(status).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByTestId("reports-facts-banner")).not.toBeInTheDocument();
  });

  it("goes away and reloads the reports when the upgrade is done", async () => {
    vi.spyOn(reportsApi, "factsStatus").mockResolvedValue({ ...DEMO_FACTS, outdated_rows: 501 });
    const { client, invalidate } = mount({ ...DEMO_FACTS, outdated_rows: 501 });
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
