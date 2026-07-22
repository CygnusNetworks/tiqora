import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import {
  AdminResourcePage,
  type AdminResourcePageProps,
} from "./AdminResourcePage";
import type { AdminListParams, AdminPage } from "@/lib/api";

type Row = { id: number; name: string; valid_id: number };
type Create = { name: string };
type Update = { name?: string };

/** Build a paginated list mock that slices a synthetic table of `total` rows. */
function makeChunkedListMock(total: number) {
  return vi.fn().mockImplementation(
    async (params?: AdminListParams): Promise<AdminPage<Row>> => {
      const page = params?.page ?? 1;
      const pageSize = params?.pageSize ?? 25;
      const start = (page - 1) * pageSize;
      const end = Math.min(start + pageSize, total);
      const items: Row[] = [];
      for (let i = start; i < end; i++) {
        items.push({ id: i + 1, name: `Row ${i + 1}`, valid_id: 1 });
      }
      return { items, total, page, page_size: pageSize };
    },
  );
}

function renderPage(
  props: Partial<AdminResourcePageProps<Row, Create, Update>> = {},
  listImpl?: ReturnType<typeof vi.fn>,
) {
  const list =
    listImpl ??
    vi.fn().mockResolvedValue({
      items: [
        { id: 1, name: "Alpha", valid_id: 1 },
        { id: 2, name: "Beta", valid_id: 1 },
      ],
      total: 2,
      page: 1,
      page_size: 25,
    });
  const create = vi.fn();
  const update = vi.fn();
  const deactivate = vi.fn();

  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });

  const api = { list, create, update, deactivate };

  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <AdminResourcePage<Row, Create, Update>
          resourceKey="test-resource"
          title="Test resource"
          newLabel="New item"
          api={api}
          idOf={(r) => r.id}
          columns={[{ key: "name", header: "Name", render: (r) => r.name }]}
          fields={[{ name: "name", label: "Name", type: "text", required: true }]}
          toFormValues={(row) => (row ? { name: row.name } : { name: "" })}
          toCreateBody={(v) => ({ name: v.name as string })}
          toUpdateBody={(v) => ({ name: v.name as string })}
          {...props}
        />
      </I18nextProvider>
    </QueryClientProvider>,
  );

  return { list, create, update, deactivate };
}

describe("AdminResourcePage", () => {
  beforeEach(() => {
    vi.useRealTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("does not render a search input or checkbox column when opts are off", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-test-resource-page")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("admin-test-resource-search")).not.toBeInTheDocument();
    expect(screen.queryByTestId("admin-select-all")).not.toBeInTheDocument();
    expect(screen.queryByTestId("admin-row-select-1")).not.toBeInTheDocument();
    expect(screen.queryByTestId("admin-bulk-bar")).not.toBeInTheDocument();
    // Page size select includes 500 for large lists.
    const pageSize = screen.getByTestId("admin-test-resource-page-size");
    expect(within(pageSize).getByRole("option", { name: "500" })).toBeInTheDocument();
  });

  it("sends search in list params when searchable", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { list } = renderPage({ searchable: true });

    await waitFor(() => expect(list).toHaveBeenCalled());
    list.mockClear();

    const input = screen.getByTestId("admin-test-resource-search");
    fireEvent.change(input, { target: { value: "alice" } });

    // Debounced — not yet.
    expect(list).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(350);

    await waitFor(() => {
      expect(list).toHaveBeenCalledWith(
        expect.objectContaining({ search: "alice", page: 1 }),
        expect.anything(),
      );
    });
    vi.useRealTimers();
  });

  it("renders bulk bar and calls run with selected ids", async () => {
    const run = vi.fn().mockResolvedValue(undefined);
    renderPage({
      bulkActions: [{ key: "valid", label: "Set valid", run }],
    });

    await waitFor(() => {
      expect(screen.getByTestId("admin-row-select-1")).toBeInTheDocument();
    });

    // No bar until something is selected.
    expect(screen.queryByTestId("admin-bulk-bar")).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId("admin-row-select-1"));
    fireEvent.click(screen.getByTestId("admin-row-select-2"));

    const bar = await screen.findByTestId("admin-bulk-bar");
    expect(within(bar).getByTestId("admin-bulk-count")).toBeInTheDocument();
    expect(screen.getByTestId("admin-bulk-count").textContent).toMatch(/2/);

    fireEvent.click(screen.getByTestId("admin-bulk-action-valid"));

    await waitFor(() => {
      expect(run).toHaveBeenCalledTimes(1);
    });
    const ids = run.mock.calls[0][0] as Array<number | string>;
    expect(ids).toEqual(expect.arrayContaining([1, 2]));
    expect(ids).toHaveLength(2);
    // run also receives a ctx object with an onProgress callback.
    const ctx = run.mock.calls[0][1] as { onProgress?: (done: number, total: number) => void };
    expect(typeof ctx.onProgress).toBe("function");

    // Selection cleared after action; count/action buttons gone but the
    // success status stays visible in the bar.
    await waitFor(() => {
      expect(screen.queryByTestId("admin-bulk-count")).not.toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-bulk-status").textContent).toMatch(/2/);
  });

  it("shows busy state (disabled buttons + spinner) while a bulk action runs", async () => {
    let resolveRun!: (value: void) => void;
    const run = vi.fn().mockImplementation(
      () =>
        new Promise<void>((resolve) => {
          resolveRun = resolve;
        }),
    );
    renderPage({
      bulkActions: [{ key: "valid", label: "Set valid", run }],
    });

    await waitFor(() => {
      expect(screen.getByTestId("admin-row-select-1")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("admin-row-select-1"));
    await screen.findByTestId("admin-bulk-bar");

    const button = screen.getByTestId("admin-bulk-action-valid");
    fireEvent.click(button);

    await waitFor(() => {
      expect(button).toBeDisabled();
    });

    resolveRun();
    await waitFor(() => {
      expect(screen.getByTestId("admin-bulk-status")).toBeInTheDocument();
    });
  });

  it("shows a red error status and keeps the selection when the action fails", async () => {
    const run = vi.fn().mockRejectedValue(new Error("boom"));
    renderPage({
      bulkActions: [{ key: "valid", label: "Set valid", run }],
    });

    await waitFor(() => {
      expect(screen.getByTestId("admin-row-select-1")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("admin-row-select-1"));
    await screen.findByTestId("admin-bulk-bar");
    fireEvent.click(screen.getByTestId("admin-bulk-action-valid"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-bulk-status")).toBeInTheDocument();
    });
    // Selection is retained so the user can correct and retry.
    expect(screen.getByTestId("admin-bulk-count")).toBeInTheDocument();
    expect(screen.getByTestId("admin-bulk-count").textContent).toMatch(/1/);
  });

  it("create button is a compact + with accessible name from newLabel", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-new-button")).toBeInTheDocument();
    });
    const btn = screen.getByTestId("admin-new-button");
    expect(btn).toHaveAttribute("aria-label", "New item");
    expect(btn).toHaveAttribute("title", "New item");
    // Visible label text is gone; accessible name remains.
    expect(btn.textContent?.trim()).not.toBe("New item");
    expect(screen.getByRole("button", { name: "New item" })).toBe(btn);
  });

  it("does not render sort headers when sortable is off", async () => {
    renderPage({
      columns: [{ key: "name", header: "Name", sortable: true, render: (r) => r.name }],
    });
    await waitFor(() => {
      expect(screen.getByTestId("admin-test-resource-page")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("admin-sort-name")).not.toBeInTheDocument();
  });

  it("cycles sort asc → desc → clear and forwards to list", async () => {
    const { list } = renderPage({
      sortable: true,
      columns: [{ key: "name", header: "Name", sortable: true, render: (r) => r.name }],
    });

    await waitFor(() => expect(list).toHaveBeenCalled());
    list.mockClear();

    const header = await screen.findByTestId("admin-sort-name");
    fireEvent.click(header);

    await waitFor(() => {
      expect(list).toHaveBeenCalledWith(
        expect.objectContaining({ sort: "name", order: "asc", page: 1 }),
        expect.anything(),
      );
    });
    expect(header.textContent).toMatch(/▲/);

    list.mockClear();
    fireEvent.click(header);
    await waitFor(() => {
      expect(list).toHaveBeenCalledWith(
        expect.objectContaining({ sort: "name", order: "desc", page: 1 }),
        expect.anything(),
      );
    });
    expect(header.textContent).toMatch(/▼/);

    list.mockClear();
    fireEvent.click(header);
    await waitFor(() => {
      expect(list).toHaveBeenCalled();
    });
    const lastParams = list.mock.calls[list.mock.calls.length - 1][0] as Record<string, unknown>;
    expect(lastParams.sort).toBeUndefined();
    expect(lastParams.order).toBeUndefined();
  });

  describe("Alle (all rows) chunked fetch", () => {
    it("fetches a large table in 500-row chunks and shows all rows", async () => {
      const list = makeChunkedListMock(1200);
      renderPage({ allowAllPageSize: true }, list);

      await waitFor(() => expect(list).toHaveBeenCalled());
      list.mockClear();

      const select = screen.getByTestId("admin-test-resource-page-size");
      fireEvent.change(select, { target: { value: "100000" } });

      await waitFor(() => {
        expect(screen.getByTestId("admin-row-1")).toBeInTheDocument();
        expect(screen.getByTestId("admin-row-1200")).toBeInTheDocument();
      });

      // Exactly 3 chunked requests at page_size 500 — never a 100k mega-request.
      expect(list).toHaveBeenCalledTimes(3);
      for (const call of list.mock.calls) {
        expect(call[0]).toEqual(
          expect.objectContaining({ pageSize: 500 }),
        );
        expect(call[0].pageSize).not.toBe(100_000);
      }
      expect(list).toHaveBeenNthCalledWith(
        1,
        expect.objectContaining({ page: 1, pageSize: 500 }),
        expect.anything(),
      );
      expect(list).toHaveBeenNthCalledWith(
        2,
        expect.objectContaining({ page: 2, pageSize: 500 }),
        expect.anything(),
      );
      expect(list).toHaveBeenNthCalledWith(
        3,
        expect.objectContaining({ page: 3, pageSize: 500 }),
        expect.anything(),
      );

      // All 1200 rows present in the table.
      expect(screen.getAllByTestId(/^admin-row-\d+$/)).toHaveLength(1200);
    });

    it("issues a single chunk request for a small table under Alle", async () => {
      const list = makeChunkedListMock(30);
      renderPage({ allowAllPageSize: true }, list);

      await waitFor(() => expect(list).toHaveBeenCalled());
      list.mockClear();

      fireEvent.change(screen.getByTestId("admin-test-resource-page-size"), {
        target: { value: "100000" },
      });

      await waitFor(() => {
        expect(screen.getByTestId("admin-row-1")).toBeInTheDocument();
        expect(screen.getByTestId("admin-row-30")).toBeInTheDocument();
      });

      expect(list).toHaveBeenCalledTimes(1);
      expect(list).toHaveBeenCalledWith(
        expect.objectContaining({ page: 1, pageSize: 500 }),
        expect.anything(),
      );
      expect(screen.getAllByTestId(/^admin-row-\d+$/)).toHaveLength(30);
    });

    it("keeps numeric page sizes as a single request at that exact size", async () => {
      const list = makeChunkedListMock(100);
      renderPage({ allowAllPageSize: true }, list);

      await waitFor(() => expect(list).toHaveBeenCalled());
      list.mockClear();

      fireEvent.change(screen.getByTestId("admin-test-resource-page-size"), {
        target: { value: "50" },
      });

      await waitFor(() => {
        expect(list).toHaveBeenCalledWith(
          expect.objectContaining({ page: 1, pageSize: 50 }),
          expect.anything(),
        );
      });
      expect(list).toHaveBeenCalledTimes(1);
      expect(list.mock.calls[0][0].pageSize).toBe(50);

      list.mockClear();
      fireEvent.change(screen.getByTestId("admin-test-resource-page-size"), {
        target: { value: "500" },
      });

      await waitFor(() => {
        expect(list).toHaveBeenCalledWith(
          expect.objectContaining({ page: 1, pageSize: 500 }),
          expect.anything(),
        );
      });
      expect(list).toHaveBeenCalledTimes(1);
    });

    it("renders empty state under Alle with no infinite loop", async () => {
      const list = makeChunkedListMock(0);
      renderPage({ allowAllPageSize: true }, list);

      await waitFor(() => expect(list).toHaveBeenCalled());
      list.mockClear();

      fireEvent.change(screen.getByTestId("admin-test-resource-page-size"), {
        target: { value: "100000" },
      });

      await waitFor(() => {
        expect(screen.getByText(/No records yet|Noch keine Einträge/)).toBeInTheDocument();
      });

      // One request only — never loops on empty.
      expect(list).toHaveBeenCalledTimes(1);
      expect(list).toHaveBeenCalledWith(
        expect.objectContaining({ page: 1, pageSize: 500 }),
        expect.anything(),
      );
      expect(screen.queryByTestId(/^admin-row-\d+$/)).not.toBeInTheDocument();
    });
  });
});
