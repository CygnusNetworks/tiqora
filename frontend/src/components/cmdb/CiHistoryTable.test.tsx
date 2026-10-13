import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { api } from "@/lib/api";
import type { CiHistoryPage } from "@/lib/cmdbApi";
import { CiHistoryTable } from "./CiHistoryTable";

const row = (id: number, event: string, detail: string) => ({
  id,
  event,
  detail,
  version_id: null,
  created_by: 1,
  created_at: "2026-10-08T09:30:00",
  created_by_name: "Ada Agent",
});

const calls: Array<Record<string, unknown>> = [];

function serve(pages: CiHistoryPage[]) {
  let i = 0;
  vi.spyOn(api, "request").mockImplementation(async (_m: string, _p: string, opts?: { query?: Record<string, unknown> }) => {
    calls.push(opts?.query ?? {});
    return pages[Math.min(i++, pages.length - 1)] as never;
  });
}

function view() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <CiHistoryTable ciId={7} pageSize={2} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(async () => {
  calls.length = 0;
  await i18n.changeLanguage("de");
});
afterEach(() => vi.restoreAllMocks());

describe("CiHistoryTable", () => {
  it("shows a German label per Znuny event and pages with before_id", async () => {
    serve([
      { has_more: true, items: [row(30, "LinkAdd", "DependsOn"), row(29, "VersionCreate", "2")] },
      { has_more: false, items: [row(28, "ConfigItemCreate", "SRV-0001")] },
    ]);
    view();
    expect(await screen.findByTestId("ci-history-row-30")).toHaveTextContent("Verknüpfung hinzugefügt");
    expect(screen.getByTestId("ci-history-row-29")).toHaveTextContent("Neue Version");
    expect(calls[0]).toMatchObject({ limit: 2 });
    expect(calls[0].before_id).toBeUndefined();
    fireEvent.click(screen.getByTestId("ci-history-more"));
    expect(await screen.findByTestId("ci-history-row-28")).toHaveTextContent("CI angelegt");
    expect(calls[1]).toMatchObject({ before_id: 29, limit: 2 });
    await waitFor(() => expect(screen.queryByTestId("ci-history-more")).not.toBeInTheDocument());
  });

  it("falls back to the raw event name for an unknown event", async () => {
    serve([{ has_more: false, items: [row(5, "SomethingNew", "x")] }]);
    view();
    expect(await screen.findByTestId("ci-history-row-5")).toHaveTextContent("SomethingNew");
  });
});
