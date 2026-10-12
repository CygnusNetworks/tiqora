import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { ScimEvent, ScimStatus, ScimToken } from "@/lib/identityApi";
import { ProvisioningTab } from "./ProvisioningTab";
import { makeProvider } from "./testFixtures";

const getStatus = vi.fn();
const createToken = vi.fn();
const revokeToken = vi.fn();
const listAccounts = vi.fn();
const listGroups = vi.fn();
const listEvents = vi.fn();
const update = vi.fn();

vi.mock("@/lib/identityApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/identityApi")>();
  return {
    ...actual,
    identityApi: {
      getScimStatus: (...a: unknown[]) => getStatus(...a),
      createScimToken: (...a: unknown[]) => createToken(...a),
      revokeScimToken: (...a: unknown[]) => revokeToken(...a),
      listScimAccounts: (...a: unknown[]) => listAccounts(...a),
      listScimGroups: (...a: unknown[]) => listGroups(...a),
      listScimEvents: (...a: unknown[]) => listEvents(...a),
      update: (...a: unknown[]) => update(...a),
    },
  };
});

function wrap(ui: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>{ui}</I18nextProvider>
    </QueryClientProvider>,
  );
}

const token: ScimToken = {
  id: 7,
  name: "Entra ID",
  user_kind: "agent",
  token_hint: "1a2b",
  created_at: "2026-09-01T08:00:00Z",
  created_by_name: "Jane Doe",
  expires_at: "2027-09-01T08:00:00Z",
  revoked_at: null,
  last_used_at: null,
  active: true,
};

function status(over: Partial<ScimStatus> = {}): ScimStatus {
  return {
    base_url: "https://tickets.example.org/scim/v2",
    available: true,
    reason: null,
    enabled: true,
    adopt_existing: false,
    tokens: [token],
    users: 3,
    active_users: 2,
    groups: 1,
    pending_sync: 0,
    last_activity_at: null,
    ...over,
  };
}

function event(id: number, over: Partial<ScimEvent> = {}): ScimEvent {
  return {
    id,
    created: "2026-10-12T07:15:00Z",
    method: "POST",
    resource: "Users",
    operation: "create",
    status: 201,
    scim_id: null,
    user_kind: "agent",
    user_id: null,
    login: "jane.doe",
    detail: null,
    ...over,
  };
}

const provider = makeProvider({ id: 5, kind: "oidc", source: "db", audience: "agent", scim_enabled: true });

beforeEach(async () => {
  vi.clearAllMocks();
  await i18n.changeLanguage("de");
  getStatus.mockResolvedValue(status());
  listAccounts.mockResolvedValue({ total: 0, items: [] });
  listGroups.mockResolvedValue({ total: 0, items: [] });
  listEvents.mockResolvedValue([]);
});

async function openDialog() {
  wrap(<ProvisioningTab provider={provider} onSaved={vi.fn()} />);
  fireEvent.click(await screen.findByTestId("scim-token-new"));
}

describe("ProvisioningTab", () => {
  it("shows the token exactly once and forgets it on close", async () => {
    createToken.mockResolvedValue({ token: "tqscim_secretvalue", record: token });
    await openDialog();
    fireEvent.change(screen.getByTestId("scim-token-name"), { target: { value: "Okta" } });
    fireEvent.click(screen.getByTestId("scim-token-submit"));
    await waitFor(() => expect(screen.getByTestId("scim-token-value")).toHaveTextContent("tqscim_secretvalue"));
    expect(createToken).toHaveBeenCalledWith(5, { name: "Okta", user_kind: "agent", expires_in_days: 365 });
    expect(screen.getByTestId("scim-token-result")).toHaveTextContent("nur jetzt angezeigt");
    fireEvent.click(screen.getByTestId("scim-token-close"));
    await waitFor(() => expect(screen.queryByTestId("scim-token-value")).toBeNull());
    expect(document.body.textContent).not.toContain("tqscim_secretvalue");
    // reopening starts with an empty form, not the old token
    fireEvent.click(screen.getByTestId("scim-token-new"));
    expect(await screen.findByTestId("scim-token-name")).toHaveValue("");
    expect(screen.queryByTestId("scim-token-value")).toBeNull();
  });

  it("offers only the kinds in the provider audience", async () => {
    await openDialog();
    fireEvent.click(await screen.findByTestId("scim-token-kind"));
    const menu = await screen.findByTestId("scim-token-kind-menu");
    expect(within(menu).getByTestId("scim-token-kind-menu-option-agent")).toBeInTheDocument();
    expect(within(menu).queryByTestId("scim-token-kind-menu-option-customer")).toBeNull();
  });

  it("offers both kinds for a provider with both audiences", async () => {
    wrap(<ProvisioningTab provider={{ ...provider, audience: "both" }} onSaved={vi.fn()} />);
    fireEvent.click(await screen.findByTestId("scim-token-new"));
    fireEvent.click(await screen.findByTestId("scim-token-kind"));
    const menu = await screen.findByTestId("scim-token-kind-menu");
    expect(within(menu).getByTestId("scim-token-kind-menu-option-agent")).toBeInTheDocument();
    expect(within(menu).getByTestId("scim-token-kind-menu-option-customer")).toBeInTheDocument();
  });

  it("rejects an out-of-range validity before calling the API", async () => {
    await openDialog();
    fireEvent.change(screen.getByTestId("scim-token-name"), { target: { value: "x" } });
    fireEvent.change(screen.getByTestId("scim-token-days"), { target: { value: "731" } });
    expect(screen.getByTestId("scim-token-days-error")).toBeInTheDocument();
    expect(screen.getByTestId("scim-token-submit")).toBeDisabled();
    expect(createToken).not.toHaveBeenCalled();
  });

  it("shows the read-only notice for env providers", async () => {
    getStatus.mockResolvedValue(status({ available: false, reason: "x", tokens: [] }));
    wrap(<ProvisioningTab provider={makeProvider({ source: "env", read_only: true })} onSaved={vi.fn()} />);
    expect(await screen.findByTestId("scim-unavailable")).toHaveTextContent("Umgebungsvariablen");
    expect(screen.queryByTestId("scim-token-new")).toBeNull();
    expect(listAccounts).not.toHaveBeenCalled();
  });

  it("revokes after confirmation", async () => {
    revokeToken.mockResolvedValue(undefined);
    wrap(<ProvisioningTab provider={provider} onSaved={vi.fn()} />);
    fireEvent.click(await screen.findByTestId("scim-token-revoke-7"));
    expect(revokeToken).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.getAllByRole("button", { name: "Widerrufen" })).toHaveLength(2));
    const buttons = screen.getAllByRole("button", { name: "Widerrufen" });
    fireEvent.click(buttons[buttons.length - 1]);
    await waitFor(() => expect(revokeToken).toHaveBeenCalledWith(5, 7));
  });

  it("does not revoke when the confirmation is cancelled", async () => {
    wrap(<ProvisioningTab provider={provider} onSaved={vi.fn()} />);
    fireEvent.click(await screen.findByTestId("scim-token-revoke-7"));
    fireEvent.click(await screen.findByRole("button", { name: "Abbrechen" }));
    await waitFor(() => expect(screen.queryByRole("button", { name: "Abbrechen" })).toBeNull());
    expect(revokeToken).not.toHaveBeenCalled();
  });

  it("lists failed events only when filtered", async () => {
    listEvents.mockImplementation(async (_id: number, params: { failures_only?: boolean }) =>
      params.failures_only
        ? [event(2, { status: 409, operation: "conflict", login: null })]
        : [event(2, { status: 409, operation: "conflict", login: null }), event(1)],
    );
    wrap(<ProvisioningTab provider={provider} onSaved={vi.fn()} />);
    await waitFor(() => expect(screen.getAllByTestId("scim-event-row")).toHaveLength(2));
    fireEvent.click(screen.getByTestId("scim-events-failures"));
    await waitFor(() => expect(screen.getAllByTestId("scim-event-row")).toHaveLength(1));
    expect(screen.getByTestId("scim-event-row")).toHaveTextContent("409");
    expect(listEvents).toHaveBeenLastCalledWith(5, expect.objectContaining({ failures_only: true }), expect.anything());
  });

  it("loads further events with before_id", async () => {
    const first = Array.from({ length: 50 }, (_, i) => event(100 - i));
    listEvents.mockImplementation(async (_id: number, params: { before_id?: number }) =>
      params.before_id ? [event(50)] : first,
    );
    wrap(<ProvisioningTab provider={provider} onSaved={vi.fn()} />);
    fireEvent.click(await screen.findByTestId("scim-events-more"));
    await waitFor(() => expect(screen.getAllByTestId("scim-event-row")).toHaveLength(51));
    expect(listEvents).toHaveBeenLastCalledWith(5, expect.objectContaining({ before_id: 51 }), expect.anything());
    expect(screen.queryByTestId("scim-events-more")).toBeNull();
  });

  it("shows base URL missing hint", async () => {
    getStatus.mockResolvedValue(status({ base_url: null }));
    wrap(<ProvisioningTab provider={provider} onSaved={vi.fn()} />);
    expect(await screen.findByTestId("scim-base-url-missing")).toHaveTextContent("TIQORA_PUBLIC_BASE_URL");
    expect(screen.queryByTestId("scim-base-url-copy")).toBeNull();
  });

  it("shows the address with a copy button and the counts", async () => {
    wrap(<ProvisioningTab provider={provider} onSaved={vi.fn()} />);
    expect(await screen.findByTestId("scim-base-url")).toHaveTextContent("https://tickets.example.org/scim/v2");
    expect(screen.getByTestId("scim-base-url-copy")).toBeInTheDocument();
    expect(screen.getByTestId("scim-count-users")).toHaveTextContent("3");
    expect(screen.getByTestId("scim-count-active")).toHaveTextContent("2");
  });

  it("saves the switches through the provider PATCH", async () => {
    const saved = { ...provider, scim_adopt_existing: true };
    update.mockResolvedValue(saved);
    const onSaved = vi.fn();
    wrap(<ProvisioningTab provider={provider} onSaved={onSaved} />);
    fireEvent.click(await screen.findByTestId("scim-adopt"));
    await waitFor(() => expect(update).toHaveBeenCalledWith(5, { scim_adopt_existing: true }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
    fireEvent.click(screen.getByTestId("scim-enabled"));
    await waitFor(() => expect(update).toHaveBeenCalledWith(5, { scim_enabled: false }));
  });

  it("renders accounts and groups", async () => {
    listAccounts.mockResolvedValue({
      total: 1,
      items: [
        {
          scim_id: "a",
          user_kind: "agent",
          user_id: 11,
          login: "jane.doe",
          user_name: "jane.doe@example.org",
          external_id: null,
          active: false,
          deleted: false,
          changed: "2026-10-10T09:00:00Z",
        },
      ],
    });
    listGroups.mockResolvedValue({
      total: 1,
      items: [{ scim_id: "g", display_name: "support", external_id: null, member_count: 4, changed: "2026-10-10T09:00:00Z" }],
    });
    wrap(<ProvisioningTab provider={provider} onSaved={vi.fn()} />);
    const row = await screen.findByTestId("scim-account-row");
    expect(row).toHaveTextContent("jane.doe@example.org");
    expect(row).toHaveTextContent("Deaktiviert");
    expect(await screen.findByTestId("scim-group-row")).toHaveTextContent("support");
    expect(screen.getByTestId("scim-groups")).toHaveTextContent("scim:groups");
  });

  it("searches accounts with the typed text and resets paging", async () => {
    wrap(<ProvisioningTab provider={provider} onSaved={vi.fn()} />);
    fireEvent.change(await screen.findByTestId("scim-accounts-search"), { target: { value: "jane" } });
    await waitFor(() =>
      expect(listAccounts).toHaveBeenLastCalledWith(5, expect.objectContaining({ q: "jane", offset: 0 }), expect.anything()),
    );
  });
});
