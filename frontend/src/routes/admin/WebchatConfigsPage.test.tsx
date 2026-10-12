import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import { WebchatConfigsPage } from "./WebchatConfigsPage";
import { completeValues, defaultValues } from "@/lib/webchatForm";
import type { ChatConfig } from "@/lib/webchatApi";

const navigate = vi.fn();
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  Link: ({ children, ...rest }: { children: ReactNode; [k: string]: unknown }) => (
    <a href="#" data-testid={rest["data-testid"] as string}>
      {children}
    </a>
  ),
}));

const getChannel = vi.fn();
const updateChannel = vi.fn();
vi.mock("@/lib/api", async (orig) => {
  const mod = await orig<typeof import("@/lib/api")>();
  return {
    ...mod,
    api: {
      adminChannels: {
        get: (...a: unknown[]) => getChannel(...a),
        update: (...a: unknown[]) => updateChannel(...a),
      },
    },
  };
});

const listConfigs = vi.fn();
const deleteConfig = vi.fn();
vi.mock("@/lib/webchatApi", async (orig) => {
  const mod = await orig<typeof import("@/lib/webchatApi")>();
  return {
    ...mod,
    webchatApi: {
      listConfigs: (...a: unknown[]) => listConfigs(...a),
      deleteConfig: (...a: unknown[]) => deleteConfig(...a),
    },
  };
});

const config: ChatConfig = {
  id: 1,
  name: "Website",
  valid: true,
  identity_secret_set: false,
  public_key: "wc_key",
  embed_snippet: "<script></script>",
  session_count: 12,
  created: "2026-07-01T08:00:00Z",
  changed: "2026-07-01T08:00:00Z",
  values: {
    ...completeValues(defaultValues()),
    allowed_origins: ["https://www.example.org"],
    topics: [{ key: "support", label: "Support", queue_id: 1, priority_id: null, ai_mode: "off" }],
  },
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <WebchatConfigsPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("WebchatConfigsPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listConfigs.mockResolvedValue([config]);
    getChannel.mockResolvedValue({ channel: "webchat", enabled: false, config: {} });
    updateChannel.mockResolvedValue({ channel: "webchat", enabled: true, config: {} });
  });

  it("lists configs and toggles the master switch", async () => {
    renderPage();
    expect(await screen.findByTestId("webchat-config-link-1")).toHaveTextContent("Website");
    expect(screen.getByText("https://www.example.org")).toBeInTheDocument();
    expect(screen.getByText("12")).toBeInTheDocument();

    const sw = await screen.findByTestId("webchat-master-switch");
    await waitFor(() => expect(sw).toBeEnabled());
    expect(sw).not.toBeChecked();
    fireEvent.click(sw);
    await waitFor(() =>
      expect(updateChannel).toHaveBeenCalledWith("webchat", expect.objectContaining({ enabled: true })),
    );
    await waitFor(() => expect(screen.getByTestId("webchat-master-switch")).toBeChecked());
  });

  it("opens the editor for a new chat", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("webchat-new"));
    expect(navigate).toHaveBeenCalledWith({ to: "/admin/webchat/$configId", params: { configId: "new" } });
  });
});
