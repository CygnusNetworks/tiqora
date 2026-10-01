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
const signKeyOptions = vi.fn();
const cryptoStatus = vi.fn();

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
    adminCrypto: {
      signKeyOptions: (...args: unknown[]) => signKeyOptions(...args),
      status: (...args: unknown[]) => cryptoStatus(...args),
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
    signKeyOptions.mockReset();
    cryptoStatus.mockReset();
    cryptoStatus.mockResolvedValue([
      {
        backend: "smime",
        enabled: true,
        available: true,
        binary: { available: true, path: "openssl", version: "OpenSSL 3", reason: "" },
        paths: {},
        problems: [],
      },
    ]);
    signKeyOptions.mockResolvedValue([
      {
        value: "SMIME::Detached::1a2b3c4d.0",
        backend: "SMIME",
        method: "Detached",
        key: "1a2b3c4d.0",
        label: "SMIME-Detached: [valid] 1a2b3c4d.0 [2027-01-01] znuny@localhost",
        status: "valid",
        expires: "2027-01-01T00:00:00Z",
        emails: ["znuny@localhost"],
      },
    ]);

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

    // Open the edit drawer via the row's ⋯ menu (same pattern as other admin pages).
    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-7"));
    fireEvent.click(await screen.findByTestId("admin-row-edit-7"));

    await waitFor(() => {
      expect(screen.getByTestId("admin-form-group_id")).toBeInTheDocument();
    });

    const selectFields = [
      "group_id",
      "system_address_id",
      "salutation_id",
      "signature_id",
      "calendar_name",
    ] as const;

    // CrudDrawer renders selects as SelectMenu trigger buttons now (no
    // native <select>, and crucially no raw id number inputs).
    for (const name of selectFields) {
      const el = screen.getByTestId(`admin-form-${name}`);
      expect(el.tagName).toBe("BUTTON");
    }

    // Yes/no and valid/invalid are switches, the follow-up option a
    // radiogroup with translated labels mapped from the API's raw names.
    expect(screen.getByTestId("admin-form-follow_up_lock")).toHaveAttribute("role", "switch");
    expect(screen.getByTestId("admin-form-valid_id")).toHaveAttribute("role", "switch");
    expect(screen.getByTestId("admin-form-valid_id")).toBeChecked();
    const followUp = screen.getByTestId("admin-form-follow_up_id");
    expect(followUp).toHaveAttribute("role", "radiogroup");
    expect(within(followUp).getAllByRole("radio", { hidden: true }).map((r) => r.textContent)).toEqual([
      "Reopen",
      "Reject",
      "New ticket",
    ]);
    expect(screen.getByTestId("admin-form-follow_up_id-1")).toHaveAttribute("aria-checked", "true");

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
    openAndExpect("admin-form-calendar_name", ["Default (around the clock)", "Calendar 1"]);

    // Escalation notify fields are number inputs labelled as % notify.
    for (const name of ["first_response_notify", "update_notify", "solution_notify"] as const) {
      const el = screen.getByTestId(`admin-form-${name}`);
      expect(el.tagName).toBe("INPUT");
      expect(el).toHaveAttribute("type", "number");
    }
  });

  it("offers default sign keys from sign-key-options and keeps unknown stored values", async () => {
    list.mockResolvedValue({
      items: [{ ...sampleQueue, default_sign_key: "PGP::Detached::DEADBEEF" }],
      total: 1,
      page: 1,
      page_size: 25,
    });
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("Support")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-7"));
    fireEvent.click(await screen.findByTestId("admin-row-edit-7"));

    const trigger = await screen.findByTestId("admin-form-default_sign_key");
    expect(trigger.tagName).toBe("BUTTON");
    await waitFor(() => {
      expect(trigger).toHaveTextContent("PGP::Detached::DEADBEEF");
    });
    fireEvent.click(trigger);
    const panel = screen.getByTestId("admin-form-default_sign_key-menu");
    expect(
      within(panel).getByText("SMIME-Detached: [valid] 1a2b3c4d.0 [2027-01-01] znuny@localhost"),
    ).toBeInTheDocument();
    expect(within(panel).getByText("No automatic signing")).toBeInTheDocument();
  });

  it("shows email security fields only while a crypto backend runs", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText("Support")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-7"));
    fireEvent.click(await screen.findByTestId("admin-row-edit-7"));
    expect(await screen.findByTestId("admin-form-email_encrypt")).toBeInTheDocument();
    expect(screen.getByTestId("admin-form-default_sign_key")).toBeInTheDocument();
  });

  it("hides email security fields when no backend is enabled", async () => {
    cryptoStatus.mockResolvedValue([
      {
        backend: "pgp",
        enabled: false,
        available: true,
        binary: { available: true, path: "gpg", version: "gpg 2", reason: "" },
        paths: {},
        problems: [],
      },
    ]);
    renderPage();
    await waitFor(() => expect(screen.getByText("Support")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-7"));
    fireEvent.click(await screen.findByTestId("admin-row-edit-7"));
    await screen.findByTestId("admin-form-group_id");
    expect(screen.queryByTestId("admin-form-email_encrypt")).toBeNull();
    expect(screen.queryByTestId("admin-form-default_sign_key")).toBeNull();
    // …and with it the whole "Email security" tab.
    expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
      "General",
      "Mail & replies",
      "Escalation",
    ]);
  });

  async function openEdit() {
    await waitFor(() => expect(screen.getByText("Support")).toBeInTheDocument());
    fireEvent.click(screen.getByTestId("admin-row-menu-trigger-7"));
    fireEvent.click(await screen.findByTestId("admin-row-edit-7"));
    await screen.findByTestId("admin-form-group_id");
  }

  it("splits the dialog into four tabs with sentence-case labels", async () => {
    renderPage();
    await openEdit();
    await waitFor(() =>
      expect(screen.getAllByRole("tab").map((t) => t.textContent)).toEqual([
        "General",
        "Mail & replies",
        "Escalation",
        "Email security",
      ]),
    );
    expect(screen.getByRole("dialog")).toHaveTextContent('Edit queue “Support”');
    const label = document.getElementById("admin-form-unlock_timeout-label");
    expect(label).toHaveTextContent("Unlock locked tickets after");
    expect(label?.className).not.toContain("uppercase");
    // Footer summarises the escalation setup.
    expect(screen.getByTestId("admin-form-status")).toHaveTextContent("No escalation set");
  });

  it("escalation matrix humanizes minutes and gates the warning %", async () => {
    renderPage();
    await openEdit();
    fireEvent.click(screen.getByRole("tab", { name: "Escalation" }));

    const minutes = screen.getByTestId("admin-form-first_response_time");
    const pct = screen.getByTestId("admin-form-first_response_notify");
    expect(pct).toBeDisabled();
    expect(screen.getByTestId("admin-form-escalation-firstResponse-state")).toHaveTextContent("off");

    fireEvent.change(minutes, { target: { value: "150" } });
    expect(pct).toBeEnabled();
    expect(screen.getByTestId("admin-form-first_response_time-human")).toHaveTextContent(
      "= 2 h 30 min",
    );
    expect(screen.getByTestId("admin-form-escalation-firstResponse-state")).toHaveTextContent(
      "active",
    );
    fireEvent.change(pct, { target: { value: "80" } });
    expect(screen.getByTestId("admin-form-first_response_notify-human")).toHaveTextContent(
      "after 2 h",
    );
    expect(screen.getByTestId("admin-form-status")).toHaveTextContent(
      "1 escalation stage active",
    );

    fireEvent.change(minutes, { target: { value: "0" } });
    expect(pct).toBeDisabled();
    expect(screen.getByTestId("admin-form-first_response_time-human")).toHaveTextContent("");
  });

  it("saves the same API payload as before the redesign", async () => {
    update.mockResolvedValue({ ...sampleQueue });
    list.mockResolvedValue({
      items: [{ ...sampleQueue, calendar_name: "2", update_notify: 50 }],
      total: 1,
      page: 1,
      page_size: 25,
    });
    renderPage();
    await openEdit();

    fireEvent.change(screen.getByTestId("admin-form-unlock_timeout"), { target: { value: "20" } });
    fireEvent.click(screen.getByTestId("admin-form-valid_id"));
    fireEvent.click(screen.getByTestId("admin-form-follow_up_id-3"));
    fireEvent.click(screen.getByTestId("admin-form-follow_up_lock"));
    fireEvent.change(screen.getByTestId("admin-form-first_response_time"), {
      target: { value: "150" },
    });
    fireEvent.change(screen.getByTestId("admin-form-first_response_notify"), {
      target: { value: "80" },
    });
    fireEvent.change(screen.getByTestId("admin-form-solution_time"), { target: { value: "" } });
    fireEvent.click(screen.getByTestId("admin-form-email_encrypt-required"));
    fireEvent.click(screen.getByTestId("admin-form-submit"));

    await waitFor(() => expect(update).toHaveBeenCalledTimes(1));
    expect(update.mock.calls[0][0]).toBe(7);
    expect(update.mock.calls[0][1]).toEqual({
      name: "Support",
      group_id: 1,
      system_address_id: 1,
      salutation_id: 1,
      signature_id: 1,
      follow_up_id: 3,
      follow_up_lock: 1,
      unlock_timeout: 20,
      first_response_time: 150,
      first_response_notify: 80,
      update_time: null,
      // A % kept while its stage is off is passed through untouched.
      update_notify: 50,
      solution_time: null,
      solution_notify: null,
      calendar_name: "2",
      default_sign_key: null,
      email_sign_default: true,
      email_encrypt: "required",
      comments: null,
      valid_id: 2,
    });
  });
});

