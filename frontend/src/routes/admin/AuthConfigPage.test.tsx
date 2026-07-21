import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { AuthConfigPage } from "./AuthConfigPage";

const list = vi.fn();
const update = vi.fn();
const reset2fa = vi.fn();
const getGlobal = vi.fn();
const putGlobal = vi.fn();
const groupsList = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    adminAuthConfig: {
      list: (...args: unknown[]) => list(...args),
      update: (...args: unknown[]) => update(...args),
      reset2fa: (...args: unknown[]) => reset2fa(...args),
      getGlobal: (...args: unknown[]) => getGlobal(...args),
      putGlobal: (...args: unknown[]) => putGlobal(...args),
    },
    adminGroups: {
      list: (...args: unknown[]) => groupsList(...args),
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
        <AuthConfigPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const agent = {
  user_id: 42,
  login: "agent.one",
  full_name: "Agent One",
  totp_enabled: true,
  sso_eligible: false,
  enforce_2fa: false,
};

describe("AuthConfigPage", () => {
  beforeEach(() => {
    list.mockReset();
    update.mockReset();
    reset2fa.mockReset();
    getGlobal.mockReset();
    putGlobal.mockReset();
    groupsList.mockReset();

    list.mockResolvedValue({ items: [agent], total: 1, page: 1, page_size: 500 });
    getGlobal.mockResolvedValue({ enforce_all: false, enforce_group_ids: [] });
    groupsList.mockResolvedValue({
      items: [
        { id: 10, name: "group-a", valid_id: 1 },
        { id: 11, name: "group-b", valid_id: 1 },
      ],
      total: 2,
      page: 1,
      page_size: 500,
    });
    update.mockResolvedValue({ ...agent, sso_eligible: true });
    reset2fa.mockResolvedValue(undefined);
    putGlobal.mockResolvedValue({ enforce_all: true, enforce_group_ids: [10] });
  });

  it("renders agent rows and toggles call update API", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText("agent.one")).toBeInTheDocument();
    });
    expect(screen.getByText("Agent One")).toBeInTheDocument();
    expect(list).toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("auth-config-sso-42"));
    await waitFor(() => {
      expect(update).toHaveBeenCalledWith(42, { sso_eligible: true });
    });

    fireEvent.click(screen.getByTestId("auth-config-enforce-42"));
    await waitFor(() => {
      expect(update).toHaveBeenCalledWith(42, { enforce_2fa: true });
    });
  });

  it("reset confirm dialog calls reset2fa", async () => {
    renderPage();
    await waitFor(() => expect(screen.getByTestId("auth-config-reset-42")).toBeInTheDocument());

    fireEvent.click(screen.getByTestId("auth-config-reset-42"));
    await waitFor(() => {
      expect(screen.getByTestId("auth-config-reset-confirm")).toBeInTheDocument();
    });
    fireEvent.click(screen.getByTestId("auth-config-reset-confirm"));
    await waitFor(() => {
      expect(reset2fa).toHaveBeenCalledWith(42);
    });
  });

  it("saves global enforce_all and group multi-select", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("auth-config-enforce-all")).toBeInTheDocument();
      expect(screen.getByTestId("auth-config-group-10")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("auth-config-enforce-all"));
    fireEvent.click(screen.getByTestId("auth-config-group-10"));
    fireEvent.click(screen.getByTestId("auth-config-global-save"));

    await waitFor(() => {
      expect(putGlobal).toHaveBeenCalledWith({
        enforce_all: true,
        enforce_group_ids: [10],
      });
    });
  });
});
