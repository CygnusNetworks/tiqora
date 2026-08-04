import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { SystemAddressesPage } from "./SystemAddressesPage";

const list = vi.fn();
const create = vi.fn();
const update = vi.fn();
const deactivate = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(message: string) {
      super(message);
      this.name = "ApiError";
    }
  },
  api: {
    adminSystemAddresses: {
      list: (...args: unknown[]) => list(...args),
      create: (...args: unknown[]) => create(...args),
      update: (...args: unknown[]) => update(...args),
      deactivate: (...args: unknown[]) => deactivate(...args),
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
        <SystemAddressesPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const sample: {
  id: number;
  value0: string;
  value1: string;
  comments: string | null;
  valid_id: number;
  create_time: string;
  change_time: string;
} = {
  id: 1,
  value0: "support@example.com",
  value1: "Support",
  comments: null,
  valid_id: 1,
  create_time: "2026-07-01T00:00:00Z",
  change_time: "2026-07-01T00:00:00Z",
};

describe("SystemAddressesPage", () => {
  beforeEach(() => {
    list.mockReset();
    create.mockReset();
    update.mockReset();
    deactivate.mockReset();

    list.mockResolvedValue({
      items: [sample],
      total: 1,
      page: 1,
      page_size: 25,
    });
    create.mockResolvedValue({ ...sample, id: 2, value0: "ops@example.com" });
  });

  it("renders the address list", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("support@example.com")).toBeInTheDocument();
    });
    expect(screen.getByText("Support")).toBeInTheDocument();
    expect(list).toHaveBeenCalled();
    const params = list.mock.calls[0][0] as { valid?: string };
    // AdminResourcePage defaults to valid-only.
    expect(params?.valid ?? "valid").toBe("valid");
  });

  it("exposes a validity filter including invalid addresses", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("support@example.com")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-system-addresses-valid-filter")).toBeInTheDocument();
    expect(screen.getByTestId("admin-valid-valid")).toBeInTheDocument();
    expect(screen.getByTestId("admin-valid-invalid")).toBeInTheDocument();
    expect(screen.getByTestId("admin-valid-all")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("admin-valid-all"));
    await waitFor(() => {
      const last = list.mock.calls.at(-1)?.[0] as { valid?: string } | undefined;
      expect(last?.valid).toBe("all");
    });
  });

  it("opens edit drawer with email/name and status fields", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("support@example.com")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-1"));
    fireEvent.click(await screen.findByTestId("admin-row-edit-1"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-form-value0")).toBeInTheDocument();
    });
    expect(screen.getByTestId("admin-form-value0")).toHaveValue("support@example.com");
    expect(screen.getByTestId("admin-form-value1")).toHaveValue("Support");
    expect(screen.getByTestId("admin-form-valid_id")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("admin-form-valid_id"));
    const panel = await screen.findByTestId("admin-form-valid_id-menu");
    expect(within(panel).getByTestId("admin-form-valid_id-menu-option-1")).toBeInTheDocument();
    expect(within(panel).getByTestId("admin-form-valid_id-menu-option-2")).toBeInTheDocument();
  });

  it("creates an address with mapped body", async () => {
    renderPage();
    await waitFor(() => expect(list).toHaveBeenCalled());
    fireEvent.click(screen.getByTestId("admin-new-button"));
    await waitFor(() => {
      expect(screen.getByTestId("admin-form-value0")).toBeInTheDocument();
    });
    fireEvent.change(screen.getByTestId("admin-form-value0"), {
      target: { value: "ops@example.com" },
    });
    fireEvent.change(screen.getByTestId("admin-form-value1"), {
      target: { value: "Ops" },
    });
    fireEvent.click(screen.getByTestId("admin-form-submit"));
    await waitFor(() => expect(create).toHaveBeenCalled());
    const body = create.mock.calls[0][0] as {
      value0: string;
      value1: string;
      valid_id: number;
      queue_id: number;
    };
    expect(body.value0).toBe("ops@example.com");
    expect(body.value1).toBe("Ops");
    expect(body.valid_id).toBe(1);
    expect(body.queue_id).toBe(1);
  });
});
