import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { NotificationEventsPage } from "./NotificationEventsPage";
import { withSecurity } from "./notificationSecurityItems";

const list = vi.fn();
const update = vi.fn();
const status = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    listNotificationEvents: (...a: unknown[]) => list(...a),
    updateNotificationEvent: (...a: unknown[]) => update(...a),
    createNotificationEvent: vi.fn(),
    deleteNotificationEvent: vi.fn(),
    adminCrypto: { status: (...a: unknown[]) => status(...a) },
  },
}));

const transports = vi.fn();
vi.mock("@/lib/pluginPointsApi", () => ({
  pluginPointsApi: { notificationTransports: (...a: unknown[]) => transports(...a) },
}));

const baseItems = {
  Events: ["TicketCreate"],
  Recipients: ["Customer"],
  Transports: ["Email"],
};

function notification(items: Record<string, string[]>) {
  return {
    id: 7,
    name: "Ticket create notification",
    comments: null,
    valid_id: 1,
    items,
    messages: [],
  };
}

function backendStatus(pgp: boolean, smime: boolean) {
  return [
    { backend: "pgp", enabled: pgp, available: pgp, binary: {}, paths: {}, problems: [] },
    { backend: "smime", enabled: smime, available: smime, binary: {}, paths: {}, problems: [] },
  ];
}

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <NotificationEventsPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("NotificationEventsPage email security", () => {
  beforeEach(async () => {
    for (const fn of [list, update, status, transports]) fn.mockReset();
    transports.mockResolvedValue([]);
    await i18n.changeLanguage("en");
    update.mockResolvedValue(notification(baseItems));
  });

  it("writes the Znuny notification_event_item keys", async () => {
    list.mockResolvedValue([notification(baseItems)]);
    status.mockResolvedValue(backendStatus(true, false));
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Email security" }));
    const form = await screen.findByTestId("notification-email-security");
    const level = screen.getByLabelText("Email security level");
    expect(level).toBeDisabled();
    fireEvent.click(screen.getByLabelText("Enable email security"));
    // only the enabled backend is offered, like Znuny
    await waitFor(() => expect(level).toBeEnabled());
    const values = Array.from((level as HTMLSelectElement).options).map((o) => o.value);
    expect(values).toEqual(["", "PGPSign", "PGPCrypt", "PGPSignCrypt"]);
    fireEvent.change(level, { target: { value: "PGPSignCrypt" } });
    fireEvent.change(screen.getByLabelText("If encryption key/certificate is missing"), {
      target: { value: "Send" },
    });
    fireEvent.submit(form);
    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update.mock.calls[0][0]).toBe(7);
    expect(update.mock.calls[0][1]).toEqual({
      items: {
        ...baseItems,
        EmailSecuritySettings: ["1"],
        EmailSigningCrypting: ["PGPSignCrypt"],
        EmailMissingSigningKeys: ["Skip"],
        EmailMissingCryptingKeys: ["Send"],
      },
    });
  });

  it("shows the stored level and warns when its backend is off", async () => {
    list.mockResolvedValue([
      notification({
        ...baseItems,
        EmailSecuritySettings: ["1"],
        EmailSigningCrypting: ["SMIMESign"],
        EmailMissingSigningKeys: ["Skip"],
        EmailMissingCryptingKeys: ["Skip"],
      }),
    ]);
    status.mockResolvedValue(backendStatus(true, false));
    renderPage();
    expect(await screen.findByTestId("notification-security-level")).toHaveTextContent(
      "S/MIME sign only",
    );
    fireEvent.click(screen.getByRole("button", { name: "Email security" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("S/MIME is disabled");
    expect(screen.getByLabelText("Email security level")).toHaveValue("SMIMESign");
  });

  it("unchecking removes all security rows", () => {
    const items = {
      ...baseItems,
      EmailSecuritySettings: ["1"],
      EmailSigningCrypting: ["PGPSign"],
      EmailMissingSigningKeys: ["Send"],
      EmailMissingCryptingKeys: ["Skip"],
    };
    expect(
      withSecurity(items, { enabled: false, level: "PGPSign", missingSign: "", missingCrypt: "" }),
    ).toEqual(baseItems);
  });
});

describe("NotificationEventsPage Versandwege", () => {
  beforeEach(async () => {
    for (const fn of [list, update, status, transports]) fn.mockReset();
    await i18n.changeLanguage("de");
    status.mockResolvedValue(backendStatus(false, false));
    update.mockResolvedValue(notification(baseItems));
  });

  it("edits Versandwege and keeps unknown transports", async () => {
    transports.mockResolvedValue([
      { key: "Email", label: "E-Mail", plugin_id: null },
      { key: "Plugin_points_Chat", label: "Chat (Test)", plugin_id: "org.example.points" },
    ]);
    list.mockResolvedValue([notification({ ...baseItems, Transports: ["Email", "SMS"] })]);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Versandwege" }));
    const form = await screen.findByTestId("notification-transports");
    expect(await screen.findByLabelText("Chat (Test)")).not.toBeChecked();
    expect(screen.getByLabelText("E-Mail")).toBeChecked();
    expect(screen.getByTestId("notification-transport-unknown-SMS")).toHaveTextContent("SMS");
    fireEvent.click(screen.getByLabelText("Chat (Test)"));
    fireEvent.submit(form);
    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update.mock.calls[0][0]).toBe(7);
    expect(update.mock.calls[0][1]).toEqual({
      items: { ...baseItems, Transports: ["Email", "SMS", "Plugin_points_Chat"] },
    });
  });

  it("shows E-Mail checked when no Transports item exists and keeps it on save", async () => {
    transports.mockResolvedValue([
      { key: "Email", label: "E-Mail", plugin_id: null },
      { key: "Plugin_points_Chat", label: "Chat (Test)", plugin_id: "org.example.points" },
    ]);
    const { Transports: _t, ...noTransports } = baseItems;
    list.mockResolvedValue([notification(noTransports)]);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Versandwege" }));
    const form = await screen.findByTestId("notification-transports");
    expect(screen.getByLabelText("E-Mail")).toBeChecked();
    fireEvent.click(await screen.findByLabelText("Chat (Test)"));
    fireEvent.submit(form);
    await waitFor(() => expect(update).toHaveBeenCalled());
    expect(update.mock.calls[0][1]).toEqual({
      items: { ...noTransports, Transports: ["Email", "Plugin_points_Chat"] },
    });
  });

  it("blocks saving an empty Versandwege selection", async () => {
    transports.mockResolvedValue([{ key: "Email", label: "E-Mail", plugin_id: null }]);
    list.mockResolvedValue([notification({ ...baseItems, Transports: ["Email"] })]);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "Versandwege" }));
    const form = await screen.findByTestId("notification-transports");
    fireEvent.click(screen.getByLabelText("E-Mail"));
    expect(screen.getByTestId("notification-transports-empty")).toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Speichern" })).toBeDisabled();
  });
});
