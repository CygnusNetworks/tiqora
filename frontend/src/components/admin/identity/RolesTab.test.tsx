import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { api, ApiError } from "@/lib/api";
import { RolesTab } from "./RolesTab";
import { makeProvider } from "./testFixtures";

const update = vi.fn();
const managed = vi.fn();

vi.mock("@/lib/identityApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/identityApi")>();
  return {
    ...actual,
    identityApi: {
      update: (...a: unknown[]) => update(...a),
      managedAssignments: (...a: unknown[]) => managed(...a),
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

const rule = { attribute: "groups", value: "support", role_ids: [1], groups: [{ group_id: 3, permissions: ["ro" as const] }] };

beforeEach(async () => {
  vi.clearAllMocks();
  await i18n.changeLanguage("de");
  managed.mockResolvedValue([]);
  vi.spyOn(api, "request").mockImplementation((async (_method: string, path: string) =>
    path.endsWith("/roles") ? { items: [{ id: 1, name: "Agent" }, { id: 2, name: "Supervisor" }] } : { items: [{ id: 3, name: "support" }, { id: 4, name: "billing" }] }) as never);
});

describe("RolesTab", () => {
  it("hints at the SCIM attributes only when a rule uses scim:", async () => {
    const { unmount } = wrap(<RolesTab provider={makeProvider({ role_rules: [rule] })} onSaved={vi.fn()} />);
    expect(screen.queryByTestId("identity-scim-hint")).toBeNull();
    unmount();
    wrap(<RolesTab provider={makeProvider({ role_rules: [{ ...rule, attribute: "scim:groups" }] })} onSaved={vi.fn()} />);
    expect(screen.getByTestId("identity-scim-hint")).toHaveTextContent("scim:group_ids");
  });

  it("renders rules with role chips, group permissions and the rw hint", async () => {
    wrap(<RolesTab provider={makeProvider({ role_rules: [rule] })} onSaved={vi.fn()} />);
    expect(screen.getByTestId("identity-rule-0-attribute")).toHaveValue("groups");
    expect(screen.getByTestId("identity-rule-0-value")).toHaveValue("support");
    await waitFor(() => expect(screen.getByTestId("identity-rule-0-roles-chip-1")).toHaveTextContent("Agent"));
    await waitFor(() => expect(screen.getByTestId("identity-rule-0-groups-0")).toHaveTextContent("support"));
    expect(screen.getByTestId("identity-rule-0-groups-0-perm-ro")).toBeChecked();
    expect(screen.getByTestId("identity-rule-0-groups-0-perm-create")).not.toBeChecked();
    expect(screen.queryByTestId("identity-rule-0-groups-0-rw-hint")).toBeNull();
    fireEvent.click(screen.getByTestId("identity-rule-0-groups-0-perm-rw"));
    expect(screen.getByTestId("identity-rule-0-groups-0-rw-hint")).toHaveTextContent("rw enthält alle Rechte");
  });

  it("adds and removes rules and saves role_rules with sync_mode", async () => {
    const saved = makeProvider({ role_rules: [{ ...rule, groups: [] }], sync_mode: "authoritative" });
    update.mockResolvedValue(saved);
    const onSaved = vi.fn();
    wrap(<RolesTab provider={makeProvider({ role_rules: [rule] })} onSaved={onSaved} />);
    fireEvent.click(screen.getByTestId("identity-rule-add"));
    expect(screen.getByTestId("identity-rule-1")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("identity-rule-1-remove"));
    expect(screen.queryByTestId("identity-rule-1")).toBeNull();
    fireEvent.click(screen.getByTestId("identity-rule-0-groups-0-remove"));
    fireEvent.click(screen.getByTestId("identity-sync-authoritative"));
    fireEvent.click(screen.getByTestId("identity-roles-save"));
    await waitFor(() => expect(update).toHaveBeenCalledWith(1, { role_rules: [{ ...rule, groups: [] }], sync_mode: "authoritative" }));
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
  });

  it("offers the sync modes with their help texts", () => {
    wrap(<RolesTab provider={makeProvider()} onSaved={vi.fn()} />);
    expect(screen.getByLabelText(/Nur selbst vergebene Zuordnungen pflegen \(empfohlen\)/)).toBeChecked();
    expect(screen.getByLabelText(/Wie Znuny: alle Rollen und Gruppen ersetzen/)).not.toBeChecked();
    expect(screen.getByText(/Von Hand vergebene Rollen und Gruppen bleiben/)).toBeInTheDocument();
  });

  it("shows the 422 rule error under the table", async () => {
    update.mockRejectedValue(new ApiError(422, { detail: { message: "Bitte Felder prüfen.", errors: { role_rules: "Unbekannte Rolle: 9" } } }, "/x"));
    wrap(<RolesTab provider={makeProvider({ role_rules: [rule] })} onSaved={vi.fn()} />);
    fireEvent.change(screen.getByTestId("identity-rule-0-value"), { target: { value: "other" } });
    fireEvent.click(screen.getByTestId("identity-roles-save"));
    expect(await screen.findByTestId("identity-rules-error")).toHaveTextContent("Unbekannte Rolle: 9");
  });

  it("lists managed assignments", async () => {
    managed.mockResolvedValue([
      { user_id: 2, login: "idp.anna", kind: "role", target_id: 1, target_name: "Agent", permission_key: "", created: "2026-10-01T08:30:00Z" },
      { user_id: 2, login: "idp.anna", kind: "group", target_id: 3, target_name: "support", permission_key: "ro", created: "2026-10-01T08:30:00Z" },
    ]);
    wrap(<RolesTab provider={makeProvider()} onSaved={vi.fn()} />);
    const rows = await screen.findAllByTestId("identity-managed-row");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Rolle Agent");
    expect(rows[1]).toHaveTextContent("Gruppe support (ro)");
  });

  it("suggests ldap:group_dn with a readable label for LDAP providers", () => {
    wrap(<RolesTab provider={makeProvider({ kind: "ldap", role_rules: [rule] })} onSaved={vi.fn()} />);
    const suggestion = screen.getByTestId("identity-rule-0-suggest-ldap:group_dn");
    expect(suggestion).toHaveTextContent("Mitglied der LDAP-Gruppe (DN)");
    fireEvent.click(suggestion);
    expect(screen.getByTestId("identity-rule-0-attribute")).toHaveValue("ldap:group_dn");
  });

  it("suggests the attribute names of the last test for other providers", () => {
    wrap(<RolesTab provider={makeProvider({ role_rules: [rule] })} sampleAttributes={{ groups: ["a"] }} onSaved={vi.fn()} />);
    expect(screen.getByTestId("identity-rule-0-suggest-groups")).toBeInTheDocument();
    expect(screen.queryByTestId("identity-rule-0-suggest-ldap:group_dn")).toBeNull();
  });

  it("is read-only for environment providers", () => {
    wrap(<RolesTab provider={makeProvider({ read_only: true, source: "env", role_rules: [rule] })} onSaved={vi.fn()} />);
    expect(screen.queryByTestId("identity-roles-save")).toBeNull();
    expect(screen.queryByTestId("identity-rule-add")).toBeNull();
    expect(screen.getByTestId("identity-rule-0-attribute")).toBeDisabled();
    expect(screen.getByTestId("identity-rule-0-value")).toBeDisabled();
    expect(screen.getByTestId("identity-rule-0-groups-0-perm-ro")).toBeDisabled();
    expect(screen.getByTestId("identity-sync-managed")).toBeDisabled();
    expect(screen.getByTestId("identity-sync-authoritative")).toBeDisabled();
    expect(screen.queryByTestId("identity-rule-0-remove")).toBeNull();
    expect(screen.queryByTestId("identity-rule-0-groups-0-remove")).toBeNull();
    expect(screen.queryByTestId("identity-rule-0-groups-add")).toBeNull();
  });
});
