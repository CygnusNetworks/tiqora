import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { MailOutboundPage } from "./MailOutboundPage";

const getMailOutbound = vi.fn();
const putMailOutbound = vi.fn();
const testMailOutbound = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getMailOutbound: (...args: unknown[]) => getMailOutbound(...args),
    putMailOutbound: (...args: unknown[]) => putMailOutbound(...args),
    testMailOutbound: (...args: unknown[]) => testMailOutbound(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <MailOutboundPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("MailOutboundPage", () => {
  beforeEach(() => {
    getMailOutbound.mockReset();
    putMailOutbound.mockReset();
    testMailOutbound.mockReset();
  });

  it("renders form fields and shows write-only password with gesetzt when has_password", async () => {
    getMailOutbound.mockResolvedValue({
      enabled: true,
      host: "mail.example.com",
      port: 587,
      security: "starttls",
      auth_type: "password",
      auth_user: "agent@example.com",
      has_password: true,
      from_default: "Help <help@example.com>",
      timeout_seconds: 30,
      change_time: null,
      change_by: null,
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("mail-outbound-host")).toBeInTheDocument();
    });

    expect(screen.getByTestId("mail-outbound-host")).toHaveValue("mail.example.com");
    expect(screen.getByTestId("mail-outbound-port")).toHaveValue(587);
    expect(screen.getByTestId("mail-outbound-security")).toHaveValue("starttls");
    expect(screen.getByTestId("mail-outbound-enabled")).toBeChecked();

    const password = screen.getByTestId("mail-outbound-auth-password");
    expect(password).toHaveAttribute("type", "password");
    expect(password).toHaveValue("");
    expect(screen.getByTestId("mail-outbound-password-set")).toBeInTheDocument();

    // Saving without typing a password must not send auth_password.
    putMailOutbound.mockResolvedValue({
      enabled: true,
      host: "mail.example.com",
      port: 587,
      security: "starttls",
      auth_type: "password",
      auth_user: "agent@example.com",
      has_password: true,
      from_default: "Help <help@example.com>",
      timeout_seconds: 30,
    });
    fireEvent.click(screen.getByTestId("mail-outbound-save"));
    await waitFor(() => expect(putMailOutbound).toHaveBeenCalled());
    const body = putMailOutbound.mock.calls[0][0] as Record<string, unknown>;
    expect(body).not.toHaveProperty("auth_password");
  });

  it("does not show gesetzt badge when no password is stored", async () => {
    getMailOutbound.mockResolvedValue({
      enabled: false,
      host: "",
      port: 25,
      security: "none",
      auth_type: "none",
      auth_user: "",
      has_password: false,
      from_default: "",
      timeout_seconds: 60,
    });

    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("mail-outbound-host")).toBeInTheDocument();
    });
    expect(screen.queryByTestId("mail-outbound-password-set")).not.toBeInTheDocument();
  });
});
