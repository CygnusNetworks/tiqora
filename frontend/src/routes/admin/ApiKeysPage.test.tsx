import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiKeysPage } from "./ApiKeysPage";

const listKeys = vi.fn();
const createKey = vi.fn();
const updateKey = vi.fn();
const removeKey = vi.fn();
const listUsers = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(message: string) {
      super(message);
      this.name = "ApiError";
    }
  },
  api: {
    adminApiKeys: {
      list: (...args: unknown[]) => listKeys(...args),
      create: (...args: unknown[]) => createKey(...args),
      update: (...args: unknown[]) => updateKey(...args),
      remove: (...args: unknown[]) => removeKey(...args),
    },
    adminUsers: {
      list: (...args: unknown[]) => listUsers(...args),
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
        <ApiKeysPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const users = {
  items: [
    { id: 1, login: "alice", first_name: "Alice", last_name: "Adams", valid_id: 1 },
    { id: 2, login: "bob", first_name: "Bob", last_name: "Brown", valid_id: 1 },
  ],
  total: 2,
  page: 1,
  page_size: 500,
};

const unboundedKey = {
  id: 10,
  name: "CI token",
  user_id: 1,
  created: "2026-07-01T00:00:00Z",
  last_used_at: null,
  expires_at: null,
  valid: true,
};

const expiredKey = {
  id: 11,
  name: "Old token",
  user_id: 2,
  created: "2026-01-01T00:00:00Z",
  last_used_at: null,
  expires_at: "2026-02-01T00:00:00Z",
  valid: true,
};

describe("ApiKeysPage", () => {
  beforeEach(() => {
    listKeys.mockReset();
    createKey.mockReset();
    updateKey.mockReset();
    removeKey.mockReset();
    listUsers.mockReset();
    listUsers.mockResolvedValue(users);
    listKeys.mockResolvedValue({ items: [unboundedKey, expiredKey], total: 2, page: 1, page_size: 500 });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("renders 'unbefristet' for a null expiry and an expired badge for a past date", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("CI token")).toBeInTheDocument());

    expect(screen.getByText("no expiry")).toBeInTheDocument();
    expect(screen.getByTestId("admin-api-keys-expired-11")).toBeInTheDocument();
  });

  it("changes the list query when the filter SelectMenu is changed", async () => {
    renderPage();
    await waitFor(() => expect(listKeys).toHaveBeenCalledWith(
      expect.objectContaining({ valid: "valid" }),
      expect.anything(),
    ));

    fireEvent.click(screen.getByTestId("admin-api-keys-filter"));
    fireEvent.click(await screen.findByTestId("admin-api-keys-filter-panel-option-invalid"));

    await waitFor(() =>
      expect(listKeys).toHaveBeenCalledWith(
        expect.objectContaining({ valid: "invalid" }),
        expect.anything(),
      ),
    );
  });

  it("deletes a key from the row actions (no dedicated delete column) after confirm", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("CI token")).toBeInTheDocument());

    expect(screen.queryByTestId("admin-api-keys-delete-10")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-10"));
    fireEvent.click(await screen.findByTestId("admin-row-delete-10"));

    await screen.findByTestId("confirm-dialog");
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));

    await waitFor(() => expect(removeKey).toHaveBeenCalledWith(10));
  });

  it("creates a key with the 'Unbegrenzt' preset (default) sending expires_at = null", async () => {
    createKey.mockResolvedValue({ ...unboundedKey, id: 99, key: "plaintext-key" });
    renderPage();
    await waitFor(() => expect(screen.getByText("CI token")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("admin-api-keys-new"));
    await waitFor(() => expect(screen.getByTestId("admin-api-keys-form")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("admin-api-keys-form-name"), {
      target: { value: "New key" },
    });
    fireEvent.click(screen.getByTestId("admin-api-keys-form-user_id"));
    fireEvent.click(await screen.findByTestId("admin-api-keys-form-user-panel-option-2"));

    expect(screen.getByTestId("admin-api-keys-form-expiry-preview")).toHaveTextContent(
      "Valid indefinitely",
    );

    fireEvent.click(screen.getByTestId("admin-api-keys-form-submit"));

    await waitFor(() => expect(createKey).toHaveBeenCalledTimes(1));
    expect(createKey).toHaveBeenCalledWith({
      name: "New key",
      user_id: 2,
      expires_at: null,
    });
  });

  it("creates a key with the '30 Tage' preset sending expires_at ~30 days out", async () => {
    createKey.mockResolvedValue({ ...unboundedKey, id: 99, key: "plaintext-key" });
    renderPage();
    await waitFor(() => expect(screen.getByText("CI token")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("admin-api-keys-new"));
    await waitFor(() => expect(screen.getByTestId("admin-api-keys-form")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("admin-api-keys-form-name"), {
      target: { value: "New key" },
    });
    fireEvent.click(screen.getByTestId("admin-api-keys-form-user_id"));
    fireEvent.click(await screen.findByTestId("admin-api-keys-form-user-panel-option-1"));

    fireEvent.click(screen.getByTestId("admin-api-keys-form-expiry-preset-30"));
    fireEvent.click(screen.getByTestId("admin-api-keys-form-submit"));

    await waitFor(() => expect(createKey).toHaveBeenCalledTimes(1));
    const body = createKey.mock.calls[0][0] as { expires_at: string };
    const days = (new Date(body.expires_at).getTime() - Date.now()) / 86_400_000;
    expect(days).toBeGreaterThan(28.9);
    expect(days).toBeLessThan(31.1);
  });

  it("creates a key via the 'Datum…' preset sending the chosen calendar date", async () => {
    createKey.mockResolvedValue({ ...unboundedKey, id: 99, key: "plaintext-key" });
    renderPage();
    await waitFor(() => expect(screen.getByText("CI token")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("admin-api-keys-new"));
    await waitFor(() => expect(screen.getByTestId("admin-api-keys-form")).toBeInTheDocument());

    fireEvent.change(screen.getByTestId("admin-api-keys-form-name"), {
      target: { value: "New key" },
    });
    fireEvent.click(screen.getByTestId("admin-api-keys-form-user_id"));
    fireEvent.click(await screen.findByTestId("admin-api-keys-form-user-panel-option-1"));

    fireEvent.click(screen.getByTestId("admin-api-keys-form-expiry-preset-custom"));
    fireEvent.change(screen.getByTestId("admin-api-keys-form-expiry-date"), {
      target: { value: "2027-01-01" },
    });
    fireEvent.click(screen.getByTestId("admin-api-keys-form-submit"));

    await waitFor(() => expect(createKey).toHaveBeenCalledTimes(1));
    expect(createKey).toHaveBeenCalledWith({
      name: "New key",
      user_id: 1,
      expires_at: "2027-01-01T23:59:59.000Z",
    });
  });
});
