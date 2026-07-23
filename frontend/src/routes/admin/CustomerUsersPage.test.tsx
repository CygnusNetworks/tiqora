import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { CustomerUsersPage } from "./CustomerUsersPage";

const list = vi.fn();
const create = vi.fn();
const update = vi.fn();
const deactivate = vi.fn();
const bulkUpdateCustomerUsers = vi.fn();
const companyList = vi.fn();
const navigate = vi.fn();

vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
}));

vi.mock("@/lib/api", () => ({
  // Mirrors the real ApiError(status, detail, path) shape (packages/api-client)
  // so lib/bulk.ts's `new ApiError(err.status, message, err.path)` re-wrap works.
  ApiError: class ApiError extends Error {
    status: number;
    path: string;
    constructor(status: number, detail: unknown, path: string) {
      super(typeof detail === "string" ? detail : `HTTP ${status}`);
      this.name = "ApiError";
      this.status = status;
      this.path = path;
    }
  },
  api: {
    adminCustomerUsers: {
      list: (...args: unknown[]) => list(...args),
      create: (...args: unknown[]) => create(...args),
      update: (...args: unknown[]) => update(...args),
      deactivate: (...args: unknown[]) => deactivate(...args),
    },
    adminCustomerCompanies: {
      list: (...args: unknown[]) => companyList(...args),
    },
    bulkUpdateCustomerUsers: (...args: unknown[]) => bulkUpdateCustomerUsers(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <CustomerUsersPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const sampleRow = {
  id: 42,
  login: "alice@example.com",
  email: "alice@example.com",
  customer_id: "ACME",
  title: null,
  first_name: "Alice",
  last_name: "Wonder",
  phone: null,
  fax: null,
  mobile: null,
  street: null,
  zip: null,
  city: null,
  country: null,
  comments: null,
  valid_id: 1,
  create_time: "2026-07-01T00:00:00Z",
  change_time: "2026-07-01T00:00:00Z",
};

describe("CustomerUsersPage", () => {
  beforeEach(() => {
    list.mockReset();
    create.mockReset();
    update.mockReset();
    deactivate.mockReset();
    bulkUpdateCustomerUsers.mockReset();
    companyList.mockReset();
    navigate.mockReset();

    list.mockResolvedValue({
      items: [sampleRow, { ...sampleRow, id: 43, login: "bob@example.com" }],
      total: 2,
      page: 1,
      page_size: 100,
    });
    bulkUpdateCustomerUsers.mockResolvedValue({ updated: 2 });
    companyList.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 25 });
  });

  it("enables search and bulk validity actions", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("admin-customer-users-page")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-customer-users-search")).toBeInTheDocument();

    await waitFor(() => {
      expect(screen.getByTestId("admin-row-select-42")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("admin-row-select-42"));
    fireEvent.click(screen.getByTestId("admin-row-select-43"));

    await screen.findByTestId("admin-bulk-bar");
    fireEvent.click(screen.getByTestId("admin-bulk-action-invalid"));

    await waitFor(() => {
      expect(bulkUpdateCustomerUsers).toHaveBeenCalledWith({
        ids: expect.arrayContaining([42, 43]),
        valid_id: 2,
      });
    });
  });

  it("bulk valid action calls api with valid_id 1", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-row-select-42")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("admin-row-select-42"));
    await screen.findByTestId("admin-bulk-bar");
    fireEvent.click(screen.getByTestId("admin-bulk-action-valid"));

    await waitFor(() => {
      expect(bulkUpdateCustomerUsers).toHaveBeenCalledWith({
        ids: [42],
        valid_id: 1,
      });
    });
  });

  it("offers an Alle page-size option that chunk-fetches at pageSize 500", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-customer-users-page-size")).toBeInTheDocument();
    });
    // SelectField trigger button; the Alle option lives in the portal menu
    // with the 100_000 sentinel as its value — the client never sends that
    // size.
    const trigger = screen.getByTestId("admin-customer-users-page-size");
    expect(trigger.tagName).toBe("BUTTON");

    list.mockClear();
    fireEvent.click(trigger);
    fireEvent.click(
      screen.getByTestId("admin-customer-users-page-size-menu-option-100000"),
    );
    await waitFor(() => {
      expect(list).toHaveBeenCalledWith(
        expect.objectContaining({ pageSize: 500, page: 1 }),
        expect.anything(),
      );
    });
    // Small table (total 2) → single chunk, never a 100k mega-request.
    expect(list).toHaveBeenCalledTimes(1);
    expect(list.mock.calls[0][0]).not.toEqual(
      expect.objectContaining({ pageSize: 100_000 }),
    );
  });

  it("bulk GDPR action navigates to /admin/gdpr with selected logins", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("admin-row-select-42")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("admin-row-select-42"));
    fireEvent.click(screen.getByTestId("admin-row-select-43"));
    await screen.findByTestId("admin-bulk-bar");
    fireEvent.click(screen.getByTestId("admin-bulk-action-gdpr"));

    await waitFor(() => {
      expect(navigate).toHaveBeenCalledWith({
        to: "/admin/gdpr",
        search: {
          logins: expect.stringMatching(/alice@example\.com/),
        },
      });
    });
    const call = navigate.mock.calls[0][0] as { search: { logins: string } };
    const logins = call.search.logins.split(",");
    expect(logins).toEqual(expect.arrayContaining(["alice@example.com", "bob@example.com"]));
    expect(logins).toHaveLength(2);
  });

  describe("bulk chunking over the 1000-id backend cap", () => {
    /** Synthetic customer_user row matching CustomerUserAdminOut. */
    function makeRow(id: number) {
      return {
        ...sampleRow,
        id,
        login: `edu.${id}@example.com`,
        email: `edu.${id}@example.com`,
      };
    }

    /** Chunked "Alle" list mock serving `total` rows in 500-row pages. */
    function makeAllListMock(total: number) {
      return vi.fn().mockImplementation(async (params?: { page?: number; pageSize?: number }) => {
        const page = params?.page ?? 1;
        const pageSize = params?.pageSize ?? 100;
        const start = (page - 1) * pageSize;
        const end = Math.min(start + pageSize, total);
        const items = [];
        for (let i = start; i < end; i++) items.push(makeRow(i + 1));
        return { items, total, page, page_size: pageSize };
      });
    }

    async function selectAllViaAllePageSize(total: number) {
      list.mockImplementation(makeAllListMock(total));
      renderPage();
      await waitFor(() => {
        expect(screen.getByTestId("admin-customer-users-page-size")).toBeInTheDocument();
      });
      fireEvent.click(screen.getByTestId("admin-customer-users-page-size"));
      fireEvent.click(screen.getByTestId("admin-customer-users-page-size-menu-option-100000"));
      await waitFor(() => {
        expect(screen.getByTestId(`admin-row-${total}`)).toBeInTheDocument();
      });
      fireEvent.click(screen.getByTestId("admin-select-all"));
      await screen.findByTestId("admin-bulk-bar");
    }

    it("splits a 2988-id selection into 3 chunked PATCH calls (1000/1000/988)", async () => {
      bulkUpdateCustomerUsers.mockImplementation(async (body: { ids: number[] }) => ({
        updated: body.ids.length,
      }));
      await selectAllViaAllePageSize(2988);

      bulkUpdateCustomerUsers.mockClear();
      fireEvent.click(screen.getByTestId("admin-bulk-action-invalid"));

      await waitFor(() => {
        expect(screen.getByTestId("admin-bulk-status")).toBeInTheDocument();
      });
      expect(bulkUpdateCustomerUsers).toHaveBeenCalledTimes(3);
      const sizes = bulkUpdateCustomerUsers.mock.calls.map(
        (c) => (c[0] as { ids: number[] }).ids.length,
      );
      expect(sizes).toEqual([1000, 1000, 988]);
      // Every id present exactly once across the three chunks.
      const allIds = bulkUpdateCustomerUsers.mock.calls.flatMap(
        (c) => (c[0] as { ids: number[] }).ids,
      );
      expect(new Set(allIds).size).toBe(2988);
      // Success feedback reports the summed count.
      expect(screen.getByTestId("admin-bulk-status").textContent).toMatch(/2988/);
    }, 60000);

    it("stops after a failing chunk and reports progress in the error message", async () => {
      await selectAllViaAllePageSize(2988);

      bulkUpdateCustomerUsers
        .mockImplementationOnce(async () => ({ updated: 1000 }))
        .mockImplementationOnce(async () => {
          throw new ApiError(422, "boom", "/admin/customer-users/bulk");
        });
      fireEvent.click(screen.getByTestId("admin-bulk-action-invalid"));

      await waitFor(() => {
        expect(screen.getByTestId("admin-bulk-status")).toBeInTheDocument();
      });
      // Aborted after the 2nd chunk — never reached the 3rd.
      expect(bulkUpdateCustomerUsers).toHaveBeenCalledTimes(2);
      const status = screen.getByTestId("admin-bulk-status").textContent ?? "";
      expect(status).toMatch(/1000/);
      expect(status).toMatch(/2988/);
      // Selection is retained so the user can retry.
      expect(screen.getByTestId("admin-bulk-count")).toBeInTheDocument();
    }, 60000);
  });

  it("sorts by login header and toggles the indicator", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("admin-sort-login")).toBeInTheDocument();
    });

    list.mockClear();
    const header = screen.getByTestId("admin-sort-login");
    fireEvent.click(header);

    await waitFor(() => {
      expect(list).toHaveBeenCalledWith(
        expect.objectContaining({ sort: "login", order: "asc" }),
        expect.anything(),
      );
    });
    expect(header.textContent).toMatch(/▲/);

    list.mockClear();
    fireEvent.click(header);
    await waitFor(() => {
      expect(list).toHaveBeenCalledWith(
        expect.objectContaining({ sort: "login", order: "desc" }),
        expect.anything(),
      );
    });
    expect(header.textContent).toMatch(/▼/);

    // Name column maps to first_name sort key.
    list.mockClear();
    fireEvent.click(screen.getByTestId("admin-sort-first_name"));
    await waitFor(() => {
      expect(list).toHaveBeenCalledWith(
        expect.objectContaining({ sort: "first_name", order: "asc" }),
        expect.anything(),
      );
    });
  });
});
