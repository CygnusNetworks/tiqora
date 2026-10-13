import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { api } from "@/lib/api";
import type { CiVersion, CiVersionPage, CiVersionSummary } from "@/lib/cmdbApi";
import { CiVersionsPanel } from "./CiVersionsPanel";

const summary = (id: number, over: Partial<CiVersionSummary> = {}): CiVersionSummary => ({
  id,
  definition_id: 1,
  definition_version: 1,
  name: "web-01.example.org",
  deployment_state_id: 2,
  incident_state_id: 11,
  deployment_state: "Produktiv",
  incident_state: "Betriebsbereit",
  change_note: null,
  created_by: 1,
  created_by_name: "Ada Agent",
  created_at: "2026-10-08T09:30:00",
  archived: false,
  ...over,
});

const calls: Array<{ path: string; query: Record<string, unknown> }> = [];
let pages: Record<string, CiVersionPage> = {};

function serve() {
  vi.spyOn(api, "request").mockImplementation(async (_m: string, path: string, opts?: { query?: Record<string, unknown> }) => {
    const query = opts?.query ?? {};
    calls.push({ path, query });
    if (/\/diff$/.test(path)) {
      return { from_version_id: query.from_version, to_version_id: query.to_version, rows: [{ kind: "changed", path: "ip", label: "IP-Adresse", old: "192.0.2.10", new: "192.0.2.11" }] } as never;
    }
    const one = /\/versions\/(\d+)$/.exec(path);
    if (one) {
      const v: CiVersion = {
        id: Number(one[1]),
        ci_id: 7,
        definition_id: 1,
        definition_version: 3,
        name: "web-01.example.org",
        deployment_state_id: 2,
        incident_state_id: 11,
        attributes: {},
        rendered: [{ key: "hostname", label: "Hostname", path: "hostname", type: "text", portal: false, display: "alt-host", children: [], ref: null }],
        change_note: null,
        created_by: 1,
        created_at: "2026-10-08T09:30:00",
        archived: false,
      };
      return v as never;
    }
    const key = `${query.include_archived ? "arch" : "live"}:${query.before_id ?? "first"}`;
    return (pages[key] ?? { items: [], has_more: false, archived_count: 0 }) as never;
  });
}

function view() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <CiVersionsPanel ciId={7} pageSize={2} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(async () => {
  calls.length = 0;
  pages = {
    "live:first": { items: [summary(40), summary(30)], has_more: true, archived_count: 0 },
    "live:30": { items: [summary(20)], has_more: false, archived_count: 0 },
  };
  await i18n.changeLanguage("de");
  serve();
});
afterEach(() => vi.restoreAllMocks());

const versionCalls = () => calls.filter((c) => /\/versions$/.test(c.path));

describe("CiVersionsPanel", () => {
  it("loads the next page with before_id of the last shown version", async () => {
    view();
    expect(await screen.findByTestId("ci-version-40")).toBeInTheDocument();
    expect(screen.getByTestId("ci-version-30")).toBeInTheDocument();
    expect(versionCalls()[0].query.before_id).toBeUndefined();
    fireEvent.click(screen.getByTestId("ci-versions-more"));
    expect(await screen.findByTestId("ci-version-20")).toBeInTheDocument();
    expect(versionCalls()[1].query).toMatchObject({ before_id: 30 });
    await waitFor(() => expect(screen.queryByTestId("ci-versions-more")).not.toBeInTheDocument());
  });

  it("diffs two picked versions in ascending order regardless of click order", async () => {
    view();
    await screen.findByTestId("ci-version-40");
    // newest first, then the oldest: a descending click order
    fireEvent.click(screen.getByTestId("ci-version-select-40"));
    fireEvent.click(screen.getByTestId("ci-version-select-30"));
    fireEvent.click(screen.getByTestId("ci-versions-compare"));
    expect(await screen.findByTestId("ci-diff")).toHaveTextContent("192.0.2.11");
    const diffCall = calls.find((c) => /\/diff$/.test(c.path));
    expect(diffCall?.query).toEqual({ from_version: 30, to_version: 40 });
  });

  it("needs exactly two versions before it offers the comparison", async () => {
    view();
    await screen.findByTestId("ci-version-40");
    const compare = screen.getByTestId("ci-versions-compare");
    expect(compare).toBeDisabled();
    fireEvent.click(screen.getByTestId("ci-version-select-40"));
    expect(compare).toBeDisabled();
    fireEvent.click(screen.getByTestId("ci-version-select-30"));
    expect(compare).toBeEnabled();
    // a third pick drops the oldest pick instead of growing past two
    fireEvent.click(screen.getByTestId("ci-version-select-40"));
    expect(screen.getByTestId("ci-version-select-40")).not.toBeChecked();
  });

  it("renders a single version read-only with its own definition", async () => {
    view();
    await screen.findByTestId("ci-version-40");
    fireEvent.click(screen.getByTestId("ci-version-open-40"));
    await screen.findByText("alt-host");
    const shown = screen.getByTestId("ci-version-detail");
    expect(shown).toHaveTextContent("Definition 3");
    expect(shown.querySelector("input, textarea")).toBeNull();
  });

  it("offers older versions only while some are archived, then requests include_archived", async () => {
    pages = {
      ...pages,
      "live:first": { items: [summary(40)], has_more: false, archived_count: 5 },
      "arch:first": { items: [summary(40), summary(10, { archived: true })], has_more: false, archived_count: 5 },
    };
    view();
    await screen.findByTestId("ci-version-40");
    fireEvent.click(screen.getByTestId("ci-versions-archived"));
    expect(await screen.findByTestId("ci-version-10")).toHaveTextContent("archiviert");
    expect(versionCalls().some((c) => c.query.include_archived === true)).toBe(true);
    await waitFor(() => expect(screen.queryByTestId("ci-versions-archived")).not.toBeInTheDocument());
  });

  it("does not offer older versions when none are archived", async () => {
    view();
    await screen.findByTestId("ci-version-40");
    expect(screen.queryByTestId("ci-versions-archived")).not.toBeInTheDocument();
  });
});
