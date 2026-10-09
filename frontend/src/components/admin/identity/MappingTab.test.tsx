import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { api, ApiError } from "@/lib/api";
import { MappingTab } from "./MappingTab";
import { makeProvider } from "./testFixtures";
import type { LoginPlan } from "@/lib/identityApi";

const update = vi.fn();
const preview = vi.fn();

vi.mock("@/lib/identityApi", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/identityApi")>();
  return {
    ...actual,
    identityApi: {
      update: (...a: unknown[]) => update(...a),
      preview: (...a: unknown[]) => preview(...a),
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

const plan: LoginPlan = {
  user_kind: "agent", decision: "login", matched_by: "login", user_id: 7, login: "idp.anna",
  reject_code: null, reject_message: null, profile: {}, profile_changes: {},
  roles_add: [], roles_remove: [], groups_add: [], groups_remove: [],
};

beforeEach(async () => {
  vi.clearAllMocks();
  await i18n.changeLanguage("de");
  vi.spyOn(api, "request").mockImplementation((async (_method: string, path: string) =>
    path.endsWith("/roles") ? { items: [{ id: 1, name: "Agent" }] } : { items: [{ id: 3, name: "support" }] }) as never);
});

describe("MappingTab", () => {
  it("shows one row per field and hides the customer number for agent-only providers", () => {
    wrap(<MappingTab provider={makeProvider({ audience: "agent" })} sampleAttributes={null} onSaved={vi.fn()} />);
    for (const label of ["Anmeldename", "E-Mail", "Vorname", "Nachname", "Mobilnummer"]) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
    expect(screen.queryByTestId("identity-map-customer_id")).toBeNull();
    expect(screen.getByTestId("identity-map-login")).toBeInTheDocument();
  });

  it("shows the customer number for customer providers", () => {
    wrap(<MappingTab provider={makeProvider({ audience: "both" })} sampleAttributes={null} onSaved={vi.fn()} />);
    expect(screen.getByTestId("identity-map-customer_id")).toBeInTheDocument();
  });

  it("saves attribute map, match_by, jit_enabled and jit_defaults", async () => {
    const saved = makeProvider({ match_by: "email" });
    update.mockResolvedValue(saved);
    const onSaved = vi.fn();
    wrap(<MappingTab provider={makeProvider({ audience: "agent" })} sampleAttributes={null} onSaved={onSaved} />);
    const input = screen.getByTestId("identity-map-login-input");
    fireEvent.change(input, { target: { value: "upn" } });
    fireEvent.keyDown(input, { key: "Enter" });
    fireEvent.click(screen.getByTestId("identity-match-by"));
    fireEvent.click(await screen.findByTestId("identity-match-by-menu-option-email"));
    fireEvent.click(screen.getByTestId("identity-jit"));
    fireEvent.click(screen.getByTestId("identity-mapping-save"));
    await waitFor(() => expect(update).toHaveBeenCalled());
    const [id, body] = update.mock.calls[0];
    expect(id).toBe(1);
    expect(Object.keys(body).sort()).toEqual(["attribute_map", "jit_defaults", "jit_enabled", "match_by"]);
    expect(body.attribute_map.login).toEqual(["upn"]);
    expect(body.match_by).toBe("email");
    expect(body.jit_enabled).toBe(true);
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
  });

  it("reveals initial roles and groups for agents and the fixed customer number for customers", () => {
    wrap(<MappingTab provider={makeProvider({ audience: "agent" })} sampleAttributes={null} onSaved={vi.fn()} />);
    expect(screen.queryByTestId("identity-jit-details")).toBeNull();
    fireEvent.click(screen.getByTestId("identity-jit"));
    expect(screen.getByTestId("identity-jit-roles")).toBeInTheDocument();
    expect(screen.getByTestId("identity-jit-groups")).toBeInTheDocument();
    expect(screen.queryByTestId("identity-jit-customer-id")).toBeNull();
  });

  it("shows the fixed customer number field for customer providers", () => {
    wrap(<MappingTab provider={makeProvider({ audience: "customer", jit_enabled: true })} sampleAttributes={null} onSaved={vi.fn()} />);
    expect(screen.getByTestId("identity-jit-customer-id")).toBeInTheDocument();
    expect(screen.queryByTestId("identity-jit-roles")).toBeNull();
  });

  it("warns when sign-up is on without an e-mail domain restriction, and not with one", () => {
    const jit = { customer_id: "", initial_groups: [], initial_role_ids: [], update_profile_on_login: true };
    const { unmount } = wrap(
      <MappingTab provider={makeProvider({ jit_enabled: true, jit_defaults: { ...jit, allowed_email_domains: [] } })} sampleAttributes={null} onSaved={vi.fn()} />,
    );
    expect(screen.getByTestId("identity-jit-domain-warning")).toHaveTextContent(i18n.t("admin.identity.mapping.noDomainWarning"));
    unmount();
    wrap(
      <MappingTab provider={makeProvider({ jit_enabled: true, jit_defaults: { ...jit, allowed_email_domains: ["example.org"] } })} sampleAttributes={null} onSaved={vi.fn()} />,
    );
    expect(screen.queryByTestId("identity-jit-domain-warning")).toBeNull();
  });

  it("warns about account takeover when matching by e-mail without a confirmed-address requirement", () => {
    const provider = makeProvider({ match_by: "email", config: { issuer: "https://login.example.org", email_must_be_verified: false } });
    const { unmount } = wrap(<MappingTab provider={provider} sampleAttributes={null} onSaved={vi.fn()} />);
    expect(screen.getByTestId("identity-email-untrusted-warning")).toBeInTheDocument();
    unmount();
    wrap(
      <MappingTab
        provider={makeProvider({ match_by: "email", config: { issuer: "https://login.example.org", email_must_be_verified: true } })}
        sampleAttributes={null}
        onSaved={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("identity-email-untrusted-warning")).toBeNull();
  });

  it("warns about the mutable Entra e-mail only for the Entra preset matched by e-mail", () => {
    const { unmount } = wrap(
      <MappingTab provider={makeProvider({ match_by: "email", preset: "entra_id" })} sampleAttributes={null} onSaved={vi.fn()} />,
    );
    expect(screen.getByTestId("identity-entra-email-warning")).toBeInTheDocument();
    unmount();
    wrap(<MappingTab provider={makeProvider({ match_by: "login", preset: "entra_id" })} sampleAttributes={null} onSaved={vi.fn()} />);
    expect(screen.queryByTestId("identity-entra-email-warning")).toBeNull();
  });

  it("previews the login plan with unsaved changes as draft", async () => {
    preview.mockResolvedValue(plan);
    wrap(<MappingTab provider={makeProvider({ audience: "agent" })} sampleAttributes={{ upn: ["anna@example.org"] }} onSaved={vi.fn()} />);
    expect(await screen.findByTestId("identity-plan-login")).toHaveTextContent("idp.anna");
    expect(preview.mock.calls[0][1]).toEqual({ attributes: { upn: ["anna@example.org"] }, audience: "agent", draft: null });

    const input = screen.getByTestId("identity-map-login-input");
    fireEvent.change(input, { target: { value: "upn" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(preview.mock.calls.length).toBe(2), { timeout: 2000 });
    const body = preview.mock.calls[1][1];
    expect(body.draft.attribute_map.login).toEqual(["upn"]);
    expect(body.attributes).toEqual({ upn: ["anna@example.org"] });
  });

  it("shows a hint instead of the preview while no test values are taken over", () => {
    wrap(<MappingTab provider={makeProvider()} sampleAttributes={null} onSaved={vi.fn()} />);
    expect(screen.getByTestId("identity-preview-empty")).toBeInTheDocument();
    expect(preview).not.toHaveBeenCalled();
  });

  it("is read-only for environment providers", () => {
    wrap(<MappingTab provider={makeProvider({ read_only: true, source: "env" })} sampleAttributes={null} onSaved={vi.fn()} />);
    expect(screen.getByTestId("identity-env-badge")).toBeInTheDocument();
    expect(screen.queryByTestId("identity-mapping-save")).toBeNull();
    expect(screen.queryByTestId("identity-map-login-input")).toBeNull();
    expect(screen.getByTestId("identity-jit")).toBeDisabled();
  });

  it("shows a save error", async () => {
    update.mockRejectedValue(new ApiError(422, { detail: { message: "Bitte Felder prüfen.", errors: { jit_defaults: "Unbekannte Rolle: 9" } } }, "/x"));
    wrap(<MappingTab provider={makeProvider({ jit_enabled: true })} sampleAttributes={null} onSaved={vi.fn()} />);
    fireEvent.click(screen.getByTestId("identity-update-profile"));
    fireEvent.click(screen.getByTestId("identity-mapping-save"));
    expect(await screen.findByText("Unbekannte Rolle: 9")).toBeInTheDocument();
  });

  it("debounces the preview by 400 ms and collapses a burst of edits into one call", async () => {
    vi.useFakeTimers();
    try {
      preview.mockResolvedValue(plan);
      wrap(<MappingTab provider={makeProvider({ audience: "agent" })} sampleAttributes={{ upn: ["a@example.org"] }} onSaved={vi.fn()} />);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(0);
      });
      expect(preview).toHaveBeenCalledTimes(1);
      const input = screen.getByTestId("identity-map-login-input");
      for (const name of ["upn", "mail"]) {
        fireEvent.change(input, { target: { value: name } });
        fireEvent.keyDown(input, { key: "Enter" });
        await act(async () => {
          await vi.advanceTimersByTimeAsync(200);
        });
      }
      await act(async () => {
        await vi.advanceTimersByTimeAsync(199);
      });
      expect(preview).toHaveBeenCalledTimes(1);
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1);
      });
      expect(preview).toHaveBeenCalledTimes(2);
      expect(preview.mock.calls[1][1].draft.attribute_map.login).toEqual(["upn", "mail"]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("saves allowed domains, initial roles and groups, fixed customer number and the profile switch", async () => {
    update.mockResolvedValue(makeProvider());
    wrap(<MappingTab provider={makeProvider({ audience: "both", jit_enabled: true })} sampleAttributes={null} onSaved={vi.fn()} />);
    const domains = screen.getByLabelText("Erlaubte E-Mail-Domains");
    fireEvent.change(domains, { target: { value: "Example.org" } });
    fireEvent.keyDown(domains, { key: "Enter" });
    fireEvent.click(await screen.findByTestId("identity-jit-roles-add"));
    fireEvent.click(await screen.findByTestId("identity-jit-roles-add-menu-option-1"));
    fireEvent.click(await screen.findByTestId("identity-jit-groups-add"));
    fireEvent.click(await screen.findByTestId("identity-jit-groups-add-menu-option-3"));
    fireEvent.click(screen.getByTestId("identity-jit-groups-0-perm-ro"));
    fireEvent.change(screen.getByTestId("identity-jit-customer-id"), { target: { value: "K-100" } });
    fireEvent.click(screen.getByTestId("identity-update-profile"));
    fireEvent.click(screen.getByTestId("identity-mapping-save"));
    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update.mock.calls[0][1].jit_defaults).toEqual({
      allowed_email_domains: ["example.org"],
      initial_role_ids: [1],
      initial_groups: [{ group_id: 3, permissions: ["rw", "ro"] }],
      customer_id: "K-100",
      update_profile_on_login: false,
    });
  });

  it("shows an attribute_map 422 under the field rows", async () => {
    update.mockRejectedValue(new ApiError(422, { detail: { message: "Bitte Felder prüfen.", errors: { attribute_map: "Ungültiger Wert" } } }, "/x"));
    wrap(<MappingTab provider={makeProvider()} sampleAttributes={null} onSaved={vi.fn()} />);
    fireEvent.click(screen.getByTestId("identity-update-profile"));
    fireEvent.click(screen.getByTestId("identity-mapping-save"));
    expect(await screen.findByText("Ungültiger Wert")).toBeInTheDocument();
  });

  it("is completely read-only for environment providers", () => {
    wrap(
      <MappingTab
        provider={makeProvider({ read_only: true, source: "env", jit_enabled: true, audience: "both" })}
        sampleAttributes={null}
        onSaved={vi.fn()}
      />,
    );
    expect(screen.getByTestId("identity-match-by")).toBeDisabled();
    expect(screen.getByTestId("identity-update-profile")).toBeDisabled();
    expect(screen.getByTestId("identity-jit-customer-id")).toBeDisabled();
    expect(screen.queryByTestId("identity-jit-domains-input")).toBeNull();
    expect(screen.queryByTestId("identity-jit-roles-add")).toBeNull();
    expect(screen.queryByTestId("identity-jit-groups-add")).toBeNull();
    for (const key of ["login", "email", "first_name", "last_name", "customer_id", "mobile"]) {
      expect(screen.queryByTestId(`identity-map-${key}-input`)).toBeNull();
    }
  });

  it("lets the admin preview the customer path of a provider serving both", async () => {
    preview.mockResolvedValue(plan);
    wrap(<MappingTab provider={makeProvider({ audience: "both" })} sampleAttributes={{ upn: ["a@example.org"] }} onSaved={vi.fn()} />);
    await screen.findByTestId("identity-plan-login");
    expect(preview.mock.calls[0][1].audience).toBe("agent");
    fireEvent.click(screen.getByTestId("identity-preview-audience-customer"));
    await waitFor(() => expect(preview.mock.calls.at(-1)?.[1].audience).toBe("customer"), { timeout: 2000 });
  });

  it("does not offer an audience choice for single-audience providers", () => {
    wrap(<MappingTab provider={makeProvider({ audience: "agent" })} sampleAttributes={{ upn: ["a"] }} onSaved={vi.fn()} />);
    expect(screen.queryByTestId("identity-preview-audience-customer")).toBeNull();
  });

  it("warns for SAML matched by e-mail: amber, red when agents and automatic creation are involved, none for login", () => {
    const saml = { kind: "saml" as const, config: {} };
    const a = wrap(<MappingTab provider={makeProvider({ ...saml, match_by: "email", audience: "customer" })} sampleAttributes={null} onSaved={vi.fn()} />);
    let w = screen.getByTestId("identity-saml-email-warning");
    expect(w.className).toContain("escalation");
    expect(w).not.toHaveAttribute("role");
    a.unmount();
    const b = wrap(<MappingTab provider={makeProvider({ ...saml, match_by: "email", audience: "agent", jit_enabled: false })} sampleAttributes={null} onSaved={vi.fn()} />);
    expect(screen.getByTestId("identity-saml-email-warning").className).toContain("escalation");
    b.unmount();
    const c = wrap(<MappingTab provider={makeProvider({ ...saml, match_by: "email", audience: "both", jit_enabled: true })} sampleAttributes={null} onSaved={vi.fn()} />);
    w = screen.getByTestId("identity-saml-email-warning");
    expect(w.className).toContain("danger");
    expect(w).toHaveAttribute("role", "alert");
    c.unmount();
    wrap(<MappingTab provider={makeProvider({ ...saml, match_by: "login" })} sampleAttributes={null} onSaved={vi.fn()} />);
    expect(screen.queryByTestId("identity-saml-email-warning")).toBeNull();
  });
});
