import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { QueuesPage } from "./QueuesPage";

const list = vi.fn();
const create = vi.fn();
const update = vi.fn();
const deactivate = vi.fn();
const groupsList = vi.fn();
const salutationsList = vi.fn();
const signaturesList = vi.fn();
const listSystemAddresses = vi.fn();
const listFollowUpPossible = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {
    constructor(message: string) {
      super(message);
      this.name = "ApiError";
    }
  },
  api: {
    adminQueues: {
      list: (...args: unknown[]) => list(...args),
      create: (...args: unknown[]) => create(...args),
      update: (...args: unknown[]) => update(...args),
      deactivate: (...args: unknown[]) => deactivate(...args),
    },
    adminGroups: {
      list: (...args: unknown[]) => groupsList(...args),
    },
    adminSalutations: {
      list: (...args: unknown[]) => salutationsList(...args),
    },
    adminSignatures: {
      list: (...args: unknown[]) => signaturesList(...args),
    },
    listSystemAddresses: (...args: unknown[]) => listSystemAddresses(...args),
    listFollowUpPossible: (...args: unknown[]) => listFollowUpPossible(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <QueuesPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const sampleQueue = {
  id: 7,
  name: "Support",
  group_id: 1,
  unlock_timeout: 0,
  first_response_time: null,
  first_response_notify: null,
  update_time: null,
  update_notify: null,
  solution_time: null,
  solution_notify: null,
  system_address_id: 1,
  calendar_name: null,
  default_sign_key: null,
  salutation_id: 1,
  signature_id: 1,
  follow_up_id: 1,
  follow_up_lock: 0,
  comments: null,
  valid_id: 1,
  create_time: "2026-07-01T00:00:00Z",
  change_time: "2026-07-01T00:00:00Z",
};

describe("QueuesPage", () => {
  beforeEach(() => {
    list.mockReset();
    create.mockReset();
    update.mockReset();
    deactivate.mockReset();
    groupsList.mockReset();
    salutationsList.mockReset();
    signaturesList.mockReset();
    listSystemAddresses.mockReset();
    listFollowUpPossible.mockReset();

    list.mockResolvedValue({
      items: [sampleQueue],
      total: 1,
      page: 1,
      page_size: 25,
    });
    groupsList.mockResolvedValue({
      items: [{ id: 1, name: "users", comments: null, valid_id: 1 }],
      total: 1,
      page: 1,
      page_size: 500,
    });
    listSystemAddresses.mockResolvedValue([
      { id: 1, value0: "znuny@localhost", value1: "Znuny System", valid_id: 1 },
    ]);
    salutationsList.mockResolvedValue({
      items: [{ id: 1, name: "default", text: "Hi", content_type: "text/plain", comments: null, valid_id: 1 }],
      total: 1,
      page: 1,
      page_size: 500,
    });
    signaturesList.mockResolvedValue({
      items: [{ id: 1, name: "default", text: "Regards", content_type: "text/plain", comments: null, valid_id: 1 }],
      total: 1,
      page: 1,
      page_size: 500,
    });
    listFollowUpPossible.mockResolvedValue([
      { id: 1, name: "possible", valid_id: 1 },
      { id: 2, name: "reject", valid_id: 1 },
      { id: 3, name: "new ticket", valid_id: 1 },
    ]);
  });

  it("shows resolved group and system address names in the list", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Support")).toBeInTheDocument();
    });
    expect(screen.getByText("users")).toBeInTheDocument();
    expect(screen.getByText("Znuny System <znuny@localhost>")).toBeInTheDocument();
  });

  it("renders FK fields as selects with names (not raw id number inputs)", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Support")).toBeInTheDocument();
    });

    // Open the edit drawer via the row action (same pattern as other admin pages).
    const editBtn =
      screen.queryByTestId("admin-row-edit-7") ??
      screen.queryByRole("button", { name: /edit|bearbeiten/i });
    expect(editBtn).toBeTruthy();
    fireEvent.click(editBtn!);

    await waitFor(() => {
      expect(screen.getByTestId("admin-form-group_id")).toBeInTheDocument();
    });

    const selectFields = [
      "group_id",
      "system_address_id",
      "salutation_id",
      "signature_id",
      "follow_up_id",
      "follow_up_lock",
      "valid_id",
    ] as const;

    // CrudDrawer renders selects as SelectMenu trigger buttons now (no
    // native <select>, and crucially no raw id number inputs).
    for (const name of selectFields) {
      const el = screen.getByTestId(`admin-form-${name}`);
      expect(el.tagName).toBe("BUTTON");
    }

    // Options show human names, not bare numeric labels alone — open each
    // menu and look inside its portal panel.
    const openAndExpect = (testId: string, labels: string[]) => {
      fireEvent.click(screen.getByTestId(testId));
      const panel = screen.getByTestId(`${testId}-menu`);
      for (const label of labels) {
        expect(within(panel).getByText(label)).toBeInTheDocument();
      }
      // Close via outside pointerdown — Escape would also close the drawer
      // (both the menu and the Dialog listen on document keydown).
      fireEvent.pointerDown(document.body);
    };
    openAndExpect("admin-form-group_id", ["users"]);
    openAndExpect("admin-form-system_address_id", ["Znuny System <znuny@localhost>"]);
    openAndExpect("admin-form-follow_up_id", ["possible", "reject"]);

    // Escalation notify fields are number inputs labelled as % notify.
    for (const name of ["first_response_notify", "update_notify", "solution_notify"] as const) {
      const el = screen.getByTestId(`admin-form-${name}`);
      expect(el.tagName).toBe("INPUT");
      expect(el).toHaveAttribute("type", "number");
    }
  });
});
