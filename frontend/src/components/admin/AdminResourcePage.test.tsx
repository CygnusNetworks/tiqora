import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { AdminResourcePage, type AdminResourcePageProps } from "./AdminResourcePage";

type Row = { id: number; name: string; valid_id: number };
type Create = { name: string };
type Update = { name?: string };

function renderPage(props: Partial<AdminResourcePageProps<Row, Create, Update>> = {}) {
  const list = vi.fn().mockResolvedValue({
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

    // Selection cleared after action.
    await waitFor(() => {
      expect(screen.queryByTestId("admin-bulk-bar")).not.toBeInTheDocument();
    });
  });
});
