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
const keysList = vi.fn();
const resetCustomerUser2fa = vi.fn();

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
    resetCustomerUser2fa: (...args: unknown[]) => resetCustomerUser2fa(...args),
    customerCryptoKeys: {
      list: (...args: unknown[]) => keysList(...args),
    },
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
    resetCustomerUser2fa.mockReset();

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
    expect(screen.getByTestId("admin-customer-users-search-regex")).toBeInTheDocument();

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

  it("forwards regex: true to the list call only while the toggle is checked", async () => {
    renderPage();
    await waitFor(() => expect(list).toHaveBeenCalled());

    const searchInput = screen.getByTestId("admin-customer-users-search");
    const regexToggle = screen.getByTestId("admin-customer-users-search-regex");
    expect(regexToggle).not.toBeChecked();

    list.mockClear();
    fireEvent.change(searchInput, { target: { value: "^alice" } });
    await waitFor(() => {
      expect(list).toHaveBeenCalledWith(
        expect.objectContaining({ search: "^alice" }),
        expect.anything(),
      );
    });
    expect(list.mock.calls.at(-1)?.[0]).not.toHaveProperty("regex");

    list.mockClear();
    fireEvent.click(regexToggle);
    await waitFor(() => {
      expect(list).toHaveBeenCalledWith(
        expect.objectContaining({ search: "^alice", regex: true }),
        expect.anything(),
      );
    });

    list.mockClear();
    fireEvent.click(regexToggle);
    await waitFor(() => {
      expect(list).toHaveBeenCalledWith(
        expect.objectContaining({ search: "^alice" }),
        expect.anything(),
      );
    });
    expect(list.mock.calls.at(-1)?.[0]).not.toHaveProperty("regex");
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

  // Chunk sizes and the abort are lib/bulk.test.ts's: thousands of rendered rows here were only slow.
  describe("bulk action on a selection", () => {
    async function selectBoth() {
      renderPage();
      await screen.findByTestId("admin-row-select-42");
      fireEvent.click(screen.getByTestId("admin-select-all"));
      await screen.findByTestId("admin-bulk-bar");
    }

    it("reports the updated count", async () => {
      await selectBoth();
      fireEvent.click(screen.getByTestId("admin-bulk-action-invalid"));

      const status = await screen.findByTestId("admin-bulk-status");
      expect(status.textContent).toBe(i18n.t("admin.bulk.done", { count: 2 }));
      expect(bulkUpdateCustomerUsers).toHaveBeenCalledTimes(1);
    });

    it("'Alle' over two fetch pages, then select all: every id reaches the bulk call", async () => {
      // total 501 means two fetch pages of 500; short pages keep the rendered table small
      // (501 rendered rows took 39-53 s at 20 parallel runs, over the test budget).
      const pages: Record<number, number[]> = { 1: [1, 2], 2: [3] };
      list.mockImplementation(async (params?: { page?: number; pageSize?: number }) => {
        const page = params?.page ?? 1;
        const items = (pages[page] ?? []).map((id) => ({
          ...sampleRow,
          id,
          login: `edu.${id}@example.com`,
          email: `edu.${id}@example.com`,
        }));
        return { items, total: 501, page, page_size: params?.pageSize ?? 100 };
      });
      renderPage();
      fireEvent.click(await screen.findByTestId("admin-customer-users-page-size"));
      fireEvent.click(screen.getByTestId("admin-customer-users-page-size-menu-option-100000"));
      await screen.findByTestId("admin-row-3");
      expect(list).toHaveBeenCalledWith(
        expect.objectContaining({ page: 2, pageSize: 500 }),
        expect.anything(),
      );
      fireEvent.click(screen.getByTestId("admin-select-all"));
      await screen.findByTestId("admin-bulk-bar");

      fireEvent.click(screen.getByTestId("admin-bulk-action-invalid"));

      await screen.findByTestId("admin-bulk-status");
      const sent = (bulkUpdateCustomerUsers.mock.calls[0][0] as { ids: number[] }).ids;
      expect([...sent].sort()).toEqual([1, 2, 3]);
    });

    it("reports the progress of a failed action and keeps the selection for a retry", async () => {
      await selectBoth();
      bulkUpdateCustomerUsers.mockRejectedValueOnce(
        new ApiError(422, "boom", "/admin/customer-users/bulk"),
      );
      fireEvent.click(screen.getByTestId("admin-bulk-action-invalid"));

      expect((await screen.findByTestId("admin-bulk-status")).textContent).toMatch(/0\/2/);
      expect(screen.getByTestId("admin-bulk-count")).toBeInTheDocument();
    });
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

    // Name column sorts by name (last name, first name), company by company name.
    list.mockClear();
    fireEvent.click(screen.getByTestId("admin-sort-name"));
    await waitFor(() => {
      expect(list).toHaveBeenCalledWith(
        expect.objectContaining({ sort: "name", order: "asc" }),
        expect.anything(),
      );
    });
    list.mockClear();
    fireEvent.click(screen.getByTestId("admin-sort-company"));
    await waitFor(() => {
      expect(list).toHaveBeenCalledWith(
        expect.objectContaining({ sort: "company", order: "asc" }),
        expect.anything(),
      );
    });
  });

  it("opens the customer's PGP/S-MIME keys from the row menu", async () => {
    keysList.mockResolvedValue({
      pgp_enabled: true,
      smime_enabled: false,
      can_edit: true,
      pgp_keys: [],
      smime_certificates: [],
      pgp_key_id: null,
      smime_filename: null,
      problems: [],
    });
    renderPage();
    fireEvent.click(await screen.findByTestId("admin-row-menu-trigger-42"));
    fireEvent.click(await screen.findByTestId("admin-customer-user-keys-42"));
    await waitFor(() => expect(keysList).toHaveBeenCalledWith("alice@example.com", expect.anything()));
    expect(await screen.findByTestId("customer-keys-pgp")).toBeInTheDocument();
    expect(screen.getByTestId("customer-keys-pgp-upload-file")).toBeInTheDocument();
    expect(screen.queryByTestId("customer-keys-smime")).toBeNull();
  });

  it("resets the customer's two-factor sign-in only after confirming", async () => {
    resetCustomerUser2fa.mockResolvedValue(undefined);
    renderPage();
    fireEvent.click(await screen.findByTestId("admin-row-menu-trigger-42"));
    fireEvent.click(await screen.findByTestId("admin-customer-user-reset2fa-42"));

    // Cancelling must not touch the account.
    await screen.findByTestId("confirm-dialog");
    fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));
    await waitFor(() => expect(screen.queryByTestId("confirm-dialog")).toBeNull());
    expect(resetCustomerUser2fa).not.toHaveBeenCalled();

    fireEvent.click(await screen.findByTestId("admin-row-menu-trigger-42"));
    fireEvent.click(await screen.findByTestId("admin-customer-user-reset2fa-42"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(resetCustomerUser2fa).toHaveBeenCalledWith(42));
    expect(await screen.findByTestId("admin-customer-reset2fa-notice")).toHaveTextContent(
      "alice@example.com",
    );
  });

  it("shows an error notice when the two-factor reset fails", async () => {
    resetCustomerUser2fa.mockRejectedValue(new Error("boom"));
    renderPage();
    fireEvent.click(await screen.findByTestId("admin-row-menu-trigger-42"));
    fireEvent.click(await screen.findByTestId("admin-customer-user-reset2fa-42"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-confirm"));
    expect(await screen.findByTestId("admin-customer-reset2fa-notice")).toHaveTextContent(
      i18n.t("admin.customerUsers.reset2faFailed"),
    );
  });
});
