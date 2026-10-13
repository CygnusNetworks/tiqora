import type { ReactNode } from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import type { PluginAdmin } from "@/lib/pluginsApi";
import { PLUGIN_SAFE_MODE_KEY } from "@/plugins/safeMode";
import { ADMIN_PAGES, rankAdminPages } from "@/lib/adminSearch";
import { adminListFields, aiWorkerRuntime, packageReport, syncHookRow } from "@/test/pluginFixtures";
import { PluginsPage } from "./PluginsPage";

vi.mock("@tanstack/react-router", () => ({
  Link: ({ to, params, children, ...rest }: { to: string; params?: Record<string, string>; children: ReactNode; [k: string]: unknown }) => (
    <a href={Object.entries(params ?? {}).reduce((acc, [k, v]) => acc.replace(`$${k}`, v), to)} {...rest}>
      {children}
    </a>
  ),
}));

const adminList = vi.fn();
const setEnabled = vi.fn();
const reset = vi.fn();
const uploadPackage = vi.fn();
vi.mock("@/lib/pluginsApi", () => ({
  pluginsApi: {
    uploadPackage: (...a: unknown[]) => uploadPackage(...a),
    adminList: (...a: unknown[]) => adminList(...a),
    setEnabled: (...a: unknown[]) => setEnabled(...a),
    reset: (...a: unknown[]) => reset(...a),
  },
}));

const numberGenerators = vi.fn();
const setNumberGenerator = vi.fn();
vi.mock("@/lib/pluginPointsApi", () => ({
  pluginPointsApi: {
    numberGenerators: (...a: unknown[]) => numberGenerators(...a),
    setNumberGenerator: (...a: unknown[]) => setNumberGenerator(...a),
  },
}));

const UPDATED = "2026-10-10T08:00:00Z";

function adminPlugin(overrides: Partial<PluginAdmin> = {}): PluginAdmin {
  return {
    id: "org.example.hello",
    name: "Hallo-Erweiterung",
    version: "1.0.0",
    publisher: "Example Org",
    description: "Sagt Hallo.",
    homepage: null,
    support: null,
    license: null,
    categories: [],
    source: "image",
    installed_at: null,
    enabled: true,
    status: "active",
    status_message: null,
    settings_section: "plugins.org.example.hello",
    signature_status: null,
    signer: null,
    can_uninstall: false,
    migration_error: null,
    update: null,
    capabilities: [
      { kind: "extension_point", value: "core.events.subscribers" },
      { kind: "extension_point", value: "core.http.public_routes" },
      { kind: "outbound_host", value: "api.example.org" },
      { kind: "frontend_slot", value: "ticket.sidebar" },
    ],
    contributions: [],
    hooks: [],
    runtime: [{ role: "api", state: "active", version: "1.0.0", error: null, boot_attempts: 1, updated_at: UPDATED }],
    ...overrides,
  };
}

const ACTIVE = adminPlugin();
const QUARANTINED = adminPlugin({
  id: "org.example.crashy",
  name: "Absturz-Erweiterung",
  status: "quarantined",
  status_message: "Beim Start wiederholt abgestürzt – wird übersprungen, bis Sie „Erneut versuchen“ wählen.",
  settings_section: null,
  runtime: [{ role: "worker", state: "quarantined", version: "1.0.0", error: "boom", boot_attempts: 3, updated_at: UPDATED }],
});
const TRIPPED = adminPlugin({
  id: "org.example.tripped",
  name: "Gestoppte Erweiterung",
  settings_section: null,
  hooks: [
    {
      key: "event:org.example.tripped:notify",
      kind: "event",
      name: "notify",
      schedule: null,
      tripped: true,
      consecutive_failures: 5,
      total_failures: 5,
      last_error: "RuntimeError: kaputt",
      last_error_at: UPDATED,
      last_ok_at: null,
      last_run_at: UPDATED,
    },
  ],
});

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <PluginsPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

beforeEach(async () => {
  await i18n.changeLanguage("de");
  adminList.mockReset();
  setEnabled.mockReset();
  reset.mockReset();
  uploadPackage.mockReset();
  numberGenerators.mockReset();
  setNumberGenerator.mockReset();
  sessionStorage.clear();
});
afterEach(() => sessionStorage.clear());

describe("PluginsPage", () => {
  it("lists plugins with plain-language status", async () => {
    adminList.mockResolvedValue({ ...adminListFields(), safe_mode: false, plugins: [ACTIVE, QUARANTINED] });
    renderPage();
    expect(await screen.findByTestId("plugin-status-org.example.hello")).toHaveTextContent("Aktiv");
    expect(screen.getByTestId("plugin-status-org.example.crashy")).toHaveTextContent("Übersprungen");
    expect(
      screen.getByText("Beim Start wiederholt abgestürzt – wird übersprungen, bis Sie „Erneut versuchen“ wählen."),
    ).toBeInTheDocument();
  });

  it("switching off calls the API and shows the restart note", async () => {
    adminList.mockResolvedValue({ ...adminListFields(), safe_mode: false, plugins: [ACTIVE] });
    setEnabled.mockResolvedValue({
      ...ACTIVE,
      enabled: false,
      status: "restart_required",
      status_message: "Änderung wird nach einem Neustart von Tiqora wirksam.",
    });
    renderPage();
    fireEvent.click(await screen.findByRole("switch", { name: "Hallo-Erweiterung" }));
    await waitFor(() => expect(setEnabled).toHaveBeenCalledWith("org.example.hello", false));
    expect(await screen.findByText("Änderung wird nach einem Neustart von Tiqora wirksam.")).toBeInTheDocument();
    expect(screen.getByTestId("plugin-status-org.example.hello")).toHaveTextContent("Neustart nötig");
    expect(screen.getByRole("switch", { name: "Hallo-Erweiterung" })).not.toBeChecked();
  });

  it("retry resets quarantine and stopped hooks", async () => {
    adminList.mockResolvedValue({ ...adminListFields(), safe_mode: false, plugins: [QUARANTINED, TRIPPED] });
    reset.mockResolvedValue({ ...QUARANTINED, status: "disabled", status_message: null });
    renderPage();
    fireEvent.click(await screen.findByTestId("plugin-retry-org.example.crashy"));
    await waitFor(() => expect(reset).toHaveBeenCalledWith("org.example.crashy"));
    await waitFor(() => expect(screen.queryByTestId("plugin-retry-org.example.crashy")).toBeNull());
    fireEvent.click(screen.getByTestId("plugin-details-org.example.tripped"));
    expect(screen.getByText("Angehalten nach 5 Fehlern")).toBeInTheDocument();
    expect(screen.getByText("RuntimeError: kaputt")).toBeInTheDocument();
    expect(screen.getByTestId("plugin-retry-org.example.tripped")).toBeInTheDocument();
  });

  it("shows capabilities in plain words and the trust note in the header", async () => {
    adminList.mockResolvedValue({ ...adminListFields(), safe_mode: false, plugins: [ACTIVE] });
    renderPage();
    // R-TRUST-WORDING: visible before any disclosure is opened.
    expect(await screen.findByTestId("admin-plugins-trust-note")).toHaveTextContent(/keine Absicherung/);
    fireEvent.click(screen.getByTestId("plugin-details-org.example.hello"));
    expect(screen.getByText("Ruft api.example.org auf")).toBeInTheDocument();
    expect(screen.getByText("Zeigt ein Feld in der Ticketansicht")).toBeInTheDocument();
    // R-PUBLIC-ROUTES
    expect(screen.getByText("Bietet öffentlich erreichbare Schnittstellen an (ohne Anmeldung)")).toBeInTheDocument();
    expect(screen.getByText("Weboberfläche und Schnittstelle")).toBeInTheDocument();
    expect(screen.getAllByText(/keine Absicherung/)).toHaveLength(1);
  });

  it("settings button links to the generated form", async () => {
    adminList.mockResolvedValue({ ...adminListFields(), safe_mode: false, plugins: [ACTIVE, QUARANTINED] });
    renderPage();
    expect(await screen.findByTestId("plugin-settings-org.example.hello")).toHaveAttribute(
      "href",
      "/admin/settings/plugins.org.example.hello",
    );
    expect(screen.queryByTestId("plugin-settings-org.example.crashy")).toBeNull();
  });

  it("safe mode banner, browser-off note and empty state", async () => {
    adminList.mockResolvedValue({ ...adminListFields(), safe_mode: true, plugins: [] });
    sessionStorage.setItem(PLUGIN_SAFE_MODE_KEY, "1");
    renderPage();
    expect(await screen.findByTestId("admin-plugins-safe-mode")).toBeInTheDocument();
    expect(screen.getByTestId("admin-plugins-browser-off")).toBeInTheDocument();
    expect(screen.getByTestId("admin-plugins-empty")).toBeInTheDocument();
  });

  it("shows no safe-mode notes in normal operation", async () => {
    adminList.mockResolvedValue({ ...adminListFields(), safe_mode: false, plugins: [ACTIVE] });
    renderPage();
    await screen.findByTestId("plugin-card-org.example.hello");
    expect(screen.queryByTestId("admin-plugins-safe-mode")).toBeNull();
    expect(screen.queryByTestId("admin-plugins-browser-off")).toBeNull();
  });

  it("returns focus to the switch after toggling and clears an old error on success", async () => {
    adminList.mockResolvedValue({ ...adminListFields(), safe_mode: false, plugins: [ACTIVE] });
    setEnabled.mockRejectedValueOnce(new Error("x")).mockResolvedValueOnce({ ...ACTIVE, enabled: false });
    renderPage();
    const sw = await screen.findByRole("switch", { name: "Hallo-Erweiterung" });
    fireEvent.click(sw);
    expect(await screen.findByTestId("admin-plugins-action-error")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("switch", { name: "Hallo-Erweiterung" })).toHaveFocus());
    fireEvent.click(screen.getByRole("switch", { name: "Hallo-Erweiterung" }));
    await waitFor(() => expect(screen.queryByTestId("admin-plugins-action-error")).toBeNull());
    await waitFor(() => expect(screen.getByRole("switch", { name: "Hallo-Erweiterung" })).toHaveFocus());
  });

  it("moves focus to the heading when the retry button disappears", async () => {
    adminList.mockResolvedValue({ ...adminListFields(), safe_mode: false, plugins: [QUARANTINED] });
    reset.mockResolvedValue({ ...QUARANTINED, status: "disabled", status_message: null });
    renderPage();
    fireEvent.click(await screen.findByTestId("plugin-retry-org.example.crashy"));
    await waitFor(() => expect(screen.queryByTestId("plugin-retry-org.example.crashy")).toBeNull());
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Absturz-Erweiterung" })).toHaveFocus(),
    );
  });

  it("shows restart-needed muted, not in a warning colour", async () => {
    adminList.mockResolvedValue({ ...adminListFields(), safe_mode: false, plugins: [adminPlugin({ status: "restart_required", enabled: false })] });
    renderPage();
    const chip = await screen.findByTestId("plugin-status-org.example.hello");
    expect(chip.style.color).toBe("var(--color-muted)");
  });

  it("shows an error when the switch fails", async () => {
    adminList.mockResolvedValue({ ...adminListFields(), safe_mode: false, plugins: [ACTIVE] });
    setEnabled.mockRejectedValue(new Error("x"));
    renderPage();
    fireEvent.click(await screen.findByRole("switch", { name: "Hallo-Erweiterung" }));
    expect(await screen.findByTestId("admin-plugins-action-error")).toHaveTextContent(
      "Die Änderung konnte nicht gespeichert werden.",
    );
  });
});

describe("PluginsPage upload", () => {
  it("uploads a file and opens the install dialog", async () => {
    adminList.mockResolvedValue({ ...adminListFields(), safe_mode: false, plugins: [ACTIVE] });
    uploadPackage.mockResolvedValue(packageReport());
    renderPage();
    const input = await screen.findByTestId("plugin-upload-input");
    expect(input).toHaveAttribute("accept", ".tqplugin");
    const file = new File(["zip"], "tables-1.0.0.tqplugin", { type: "application/octet-stream" });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => expect(uploadPackage).toHaveBeenCalledWith(file));
    expect(await screen.findByRole("dialog", { name: "Erweiterung installieren" })).toBeInTheDocument();
    expect(screen.getByText("Tabellen-Beispiel")).toBeInTheDocument();
  });

  it("shows the server's message when the package is rejected", async () => {
    adminList.mockResolvedValue({ ...adminListFields(), safe_mode: false, plugins: [ACTIVE] });
    uploadPackage.mockRejectedValue(new Error("x"));
    renderPage();
    fireEvent.change(await screen.findByTestId("plugin-upload-input"), {
      target: { files: [new File(["x"], "x.tqplugin")] },
    });
    expect(await screen.findByTestId("plugin-upload-error")).toHaveTextContent("Das Paket konnte nicht geprüft werden.");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("shows the reason instead of the upload button when runtime install is off", async () => {
    adminList.mockResolvedValue({
      ...adminListFields({ runtime_install: false, runtime_install_reason: "Das Paketverzeichnis ist schreibgeschützt." }),
      safe_mode: false,
      plugins: [ACTIVE],
    });
    renderPage();
    expect(await screen.findByTestId("plugin-runtime-install-off")).toHaveTextContent(
      "Das Paketverzeichnis ist schreibgeschützt.",
    );
    expect(screen.queryByTestId("plugin-upload")).toBeNull();
    expect(screen.queryByTestId("plugin-upload-input")).toBeNull();
  });

  it("offers a restart button while a plugin waits for a restart", async () => {
    adminList.mockResolvedValue({
      ...adminListFields(),
      safe_mode: false,
      plugins: [adminPlugin({ status: "restart_required" })],
    });
    renderPage();
    expect(await screen.findByRole("button", { name: "Jetzt neu starten" })).toBeInTheDocument();
  });
});

describe("admin page registry", () => {
  it("the palette finds the extensions page by its name and by keywords", async () => {
    await i18n.changeLanguage("de");
    const t = (key: string) => i18n.t(key);
    for (const query of ["Erweiterungen", "safe mode", "opm"]) {
      const hits = rankAdminPages(ADMIN_PAGES, query, t).map((page) => page.slug);
      expect(hits, query).toContain("plugins");
    }
    expect(ADMIN_PAGES.find((page) => page.slug === "plugins")?.route).toBe("/admin/plugins");
  });

  it("labels every phase-2 capability", async () => {
    const keys = [
      "core.events.sync_hooks", "core.process.transition_actions", "core.process.condition_modules",
      "core.templates.placeholders", "core.tickets.number_generators", "core.dynamic_fields.types",
      "core.mail.inbound_filters", "core.mail.outbound_filters", "core.notifications.transports",
      "core.channels.drivers", "core.auth.password_providers", "core.customers.sources",
      "core.gi.operations", "core.gi.invokers", "core.gi.mappings",
    ];
    const texts = [
      "Prüft Ticketänderungen vor dem Speichern (kann sie ablehnen)", "Liefert Aktionen für Prozesse",
      "Liefert Bedingungen für Prozesse", "Liefert Platzhalter für Vorlagen", "Liefert ein Ticketnummer-Schema",
      "Liefert Arten von Zusatzfeldern", "Bearbeitet eingehende E-Mails",
      "Prüft ausgehende E-Mails (kann den Versand ablehnen)", "Liefert Versandwege für Benachrichtigungen",
      "Liefert einen Kanal", "Prüft Kennwörter bei der Anmeldung", "Liefert ein Kundenverzeichnis",
      "Liefert Webservice-Operationen", "Liefert Webservice-Aufrufe (Invoker)", "Liefert Webservice-Zuordnungen (Mapping)",
    ];
    adminList.mockResolvedValue({
      ...adminListFields(),
      safe_mode: false,
      plugins: [adminPlugin({ capabilities: keys.map((value) => ({ kind: "extension_point" as const, value })) })],
    });
    renderPage();
    fireEvent.click(await screen.findByTestId("plugin-details-org.example.hello"));
    for (const text of texts) expect(await screen.findByText(text)).toBeInTheDocument();
    expect(screen.queryByText(/Nutzt den Erweiterungspunkt/)).not.toBeInTheDocument();
  });

  it("shows sync hooks with their operation and the AI worker role", async () => {
    adminList.mockResolvedValue({
      ...adminListFields(),
      safe_mode: false,
      plugins: [
        adminPlugin({
          runtime: [aiWorkerRuntime()],
          hooks: [
            syncHookRow("org.example.hello", "before-create", "ticket.create", {
              tripped: true, consecutive_failures: 5, total_failures: 5, last_error: "boom", last_error_at: UPDATED,
            }),
          ],
        }),
      ],
    });
    renderPage();
    fireEvent.click(await screen.findByTestId("plugin-details-org.example.hello"));
    const hook = screen.getByTestId("plugin-hook-org.example.hello-sync:org.example.hello:before-create");
    expect(hook).toHaveTextContent("Prüfung vor dem Speichern");
    expect(hook).toHaveTextContent("Ticket anlegen");
    expect(hook).toHaveTextContent("Angehalten nach 5 Fehlern");
    expect(screen.getByTestId("plugin-retry-org.example.hello")).toHaveTextContent("Erneut versuchen");
    expect(screen.getByText("KI-Hintergrunddienst")).toBeInTheDocument();
    expect(screen.queryByText("ai-worker")).not.toBeInTheDocument();
  });

  it("says so when the ticket number schemes cannot be loaded", async () => {
    numberGenerators.mockRejectedValue(new Error("kaputt"));
    adminList.mockResolvedValue({
      ...adminListFields(),
      safe_mode: false,
      plugins: [
        adminPlugin({
          capabilities: [{ kind: "extension_point", value: "core.tickets.number_generators" }],
        }),
      ],
    });
    renderPage();
    expect(await screen.findByTestId("plugin-number-generator-error-org.example.hello")).toHaveTextContent(
      "Die Ticketnummer-Schemata dieser Erweiterung konnten nicht geladen werden.",
    );
  });

  it("shows the backend's refusal of a scheme (F14)", async () => {
    const { ApiError } = await import("@/lib/api");
    const reason =
      "Dieses Schema zählt nicht wie das Znuny-Schema (Datum); bei gemeinsamem Zähler nicht möglich.";
    numberGenerators.mockResolvedValue({
      active: null,
      znuny_generator: "AutoIncrement",
      available: [
        { key: "Plugin_points_Daily", label: "Tageszähler", plugin_id: "org.example.hello", example: "20261013-1" },
      ],
    });
    setNumberGenerator.mockRejectedValue(
      new ApiError(422, { detail: reason }, "/api/v1/admin/plugins/number-generators"),
    );
    adminList.mockResolvedValue({
      ...adminListFields(),
      safe_mode: false,
      plugins: [
        adminPlugin({
          capabilities: [{ kind: "extension_point", value: "core.tickets.number_generators" }],
        }),
      ],
    });
    renderPage();
    await screen.findByTestId("plugin-number-generator-org.example.hello");
    fireEvent.click(screen.getByRole("button", { name: "Für neue Tickets verwenden" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(reason);
  });

  it("switches the ticket number scheme", async () => {
    const generators = (active: string | null) => ({
      active,
      znuny_generator: "DateChecksum",
      available: [
        { key: "Plugin_points_Yearly", label: "Jahreszähler", plugin_id: "org.example.hello", example: "2026-000001" },
        { key: "Plugin_other_Seq", label: "Fortlaufend", plugin_id: "org.example.other", example: "000001" },
      ],
    });
    numberGenerators.mockResolvedValue(generators(null));
    setNumberGenerator.mockResolvedValue(generators("Plugin_points_Yearly"));
    adminList.mockResolvedValue({
      ...adminListFields(),
      safe_mode: false,
      plugins: [
        adminPlugin({
          capabilities: [{ kind: "extension_point", value: "core.tickets.number_generators" }],
        }),
      ],
    });
    renderPage();
    const box = await screen.findByTestId("plugin-number-generator-org.example.hello");
    expect(box).toHaveTextContent("Ticketnummer-Schema");
    expect(box).toHaveTextContent("Beispiel: 2026-000001");
    expect(box).not.toHaveTextContent("Fortlaufend");
    fireEvent.click(screen.getByRole("button", { name: "Für neue Tickets verwenden" }));
    await waitFor(() => expect(setNumberGenerator).toHaveBeenCalledWith("Plugin_points_Yearly"));
    expect(await screen.findByText("Wird für neue Tickets verwendet")).toBeInTheDocument();
    expect(box).toHaveTextContent("Tickets, die Znuny parallel anlegt");
    expect(box).toHaveTextContent("(DateChecksum)");
    setNumberGenerator.mockResolvedValue(generators(null));
    fireEvent.click(screen.getByRole("button", { name: "Znuny-Schema verwenden" }));
    await waitFor(() => expect(setNumberGenerator).toHaveBeenLastCalledWith(null));
  });
});
