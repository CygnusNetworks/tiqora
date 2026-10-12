import { describe, it, expect, vi, beforeEach, beforeAll, afterAll } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import type { ReactNode } from "react";
import i18n from "@/i18n";
import { WebchatConfigEditorPage } from "./WebchatConfigEditorPage";
import { completeValues, defaultValues } from "@/lib/webchatForm";
import type { ChatConfig } from "@/lib/webchatApi";

const navigate = vi.fn();
let currentId = "new";
vi.mock("@tanstack/react-router", () => ({
  useNavigate: () => navigate,
  useParams: () => ({ configId: currentId }),
  Link: ({ children, to, ...rest }: { children: ReactNode; to?: string; [k: string]: unknown }) => (
    <a href={to ?? "#"} data-testid={rest["data-testid"] as string}>
      {children}
    </a>
  ),
}));

vi.mock("@/lib/api", async (orig) => {
  const mod = await orig<typeof import("@/lib/api")>();
  return {
    ...mod,
    api: {
      listReferenceQueues: () => Promise.resolve([{ id: 1, name: "Support" }]),
      listReferencePriorities: () => Promise.resolve([{ id: 3, name: "3 normal" }]),
      adminDynamicFields: { list: () => Promise.resolve({ items: [], total: 0, page: 1, page_size: 200 }) },
    },
  };
});

const getConfig = vi.fn();
const createConfig = vi.fn();
const updateConfig = vi.fn();
const rotateKey = vi.fn();
const aiStatus = vi.fn();
const createIdentitySecret = vi.fn();
vi.mock("@/lib/webchatApi", async (orig) => {
  const mod = await orig<typeof import("@/lib/webchatApi")>();
  return {
    ...mod,
    webchatApi: {
      getConfig: (...a: unknown[]) => getConfig(...a),
      createConfig: (...a: unknown[]) => createConfig(...a),
      updateConfig: (...a: unknown[]) => updateConfig(...a),
      rotateKey: (...a: unknown[]) => rotateKey(...a),
      aiStatus: (...a: unknown[]) => aiStatus(...a),
      createIdentitySecret: (...a: unknown[]) => createIdentitySecret(...a),
    },
  };
});

const SNIPPET = '<script src="https://tickets.example.org/chat/v1/loader.js" data-tiqora-chat="wc_key" async></script>';
const saved: ChatConfig = {
  id: 7,
  name: "Website",
  valid: true,
  public_key: "wc_key",
  embed_snippet: SNIPPET,
  session_count: 0,
  created: "2026-07-01T08:00:00Z",
  changed: "2026-07-01T08:00:00Z",
  identity_secret_set: false,
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
        <WebchatConfigEditorPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

async function apiError422(errors: Record<string, string>) {
  const { ApiError } = await import("@/lib/api");
  return new ApiError(422, { detail: { errors } }, "/api/v1/admin/chat-configs");
}

describe("WebchatConfigEditorPage", () => {
  beforeAll(() => i18n.changeLanguage("de"));
  afterAll(() => i18n.changeLanguage("en"));
  beforeEach(() => {
    vi.clearAllMocks();
    currentId = "new";
    getConfig.mockResolvedValue(saved);
    aiStatus.mockResolvedValue([]);
  });

  it("creates a config and shows server errors per field", async () => {
    createConfig.mockRejectedValueOnce(
      await apiError422({ "allowed_origins.1": "Platzhalter (*) sind nicht erlaubt." }),
    );
    createConfig.mockResolvedValueOnce(saved);
    renderPage();

    fireEvent.change(await screen.findByTestId("webchat-name"), { target: { value: "  Website " } });
    fireEvent.change(screen.getByTestId("webchat-topic-0-label"), { target: { value: "Support & Hilfe" } });
    fireEvent.click(screen.getByTestId("webchat-topic-0-queue"));
    fireEvent.click(await screen.findByTestId("webchat-topic-0-queue-menu-option-1"));
    fireEvent.change(screen.getByTestId("webchat-origins"), {
      target: { value: "https://www.example.org\n\nhttps://*.example.org" },
    });

    fireEvent.click(screen.getByTestId("webchat-save"));
    // the server numbers the non-empty lines; the editor shows the textarea line
    const line = await screen.findByTestId("webchat-origins-error-3");
    expect(line).toHaveTextContent("Zeile 3: Platzhalter (*) sind nicht erlaubt.");
    expect(screen.getByTestId("webchat-origins")).toHaveAttribute("aria-invalid", "true");
    expect(createConfig).toHaveBeenCalledTimes(1);

    fireEvent.change(screen.getByTestId("webchat-origins"), { target: { value: " https://www.example.org " } });
    expect(screen.queryByTestId("webchat-origins-errors")).toBeNull();
    fireEvent.click(screen.getByTestId("webchat-save"));
    await waitFor(() => expect(createConfig).toHaveBeenCalledTimes(2));
    expect(createConfig.mock.calls[1][0]).toMatchObject({
      name: "Website",
      valid: true,
      values: {
        allowed_origins: ["https://www.example.org"],
        topics: [{ key: "support-hilfe", label: "Support & Hilfe", queue_id: 1, priority_id: null, ai_mode: "off" }],
        greeting: null,
        privacy_url: null,
        business_calendar: null,
        theme: { primary_color: "#2563eb", launcher_label: null },
      },
    });
    await waitFor(() =>
      expect(navigate).toHaveBeenCalledWith({ to: "/admin/webchat/$configId", params: { configId: "7" } }),
    );
  });

  it("embed tab copies the snippet and rotates the key after confirm", async () => {
    currentId = "7";
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    const rotated = { ...saved, public_key: "wc_new", embed_snippet: SNIPPET.replace("wc_key", "wc_new") };
    rotateKey.mockResolvedValue(rotated);
    renderPage();

    fireEvent.click(await screen.findByRole("tab", { name: "Einbetten" }));
    const code = await screen.findByTestId("webchat-embed-snippet");
    expect(code).toHaveTextContent(SNIPPET);
    expect(code.closest("pre")).not.toBeNull();

    fireEvent.click(screen.getByTestId("webchat-embed-copy"));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(SNIPPET));
    expect(await screen.findByTestId("webchat-embed-copied")).toHaveTextContent("Kopiert.");

    fireEvent.click(screen.getByTestId("webchat-embed-rotate"));
    expect(await screen.findByText("Der alte Einbettungscode funktioniert danach nicht mehr.")).toBeInTheDocument();
    expect(rotateKey).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("confirm-dialog-confirm"));
    await waitFor(() => expect(rotateKey).toHaveBeenCalledWith(7));
    await waitFor(() => expect(screen.getByTestId("webchat-embed-snippet")).toHaveTextContent("wc_new"));
  });

  it("keeps the snippet inert text and cancelling the confirm does not rotate", async () => {
    currentId = "7";
    getConfig.mockResolvedValue({ ...saved, embed_snippet: '<img src=x onerror="alert(1)">' });
    renderPage();
    fireEvent.click(await screen.findByRole("tab", { name: "Einbetten" }));
    const code = await screen.findByTestId("webchat-embed-snippet");
    expect(code.querySelector("img")).toBeNull();
    fireEvent.click(screen.getByTestId("webchat-embed-rotate"));
    fireEvent.click(await screen.findByTestId("confirm-dialog-cancel"));
    expect(rotateKey).not.toHaveBeenCalled();
  });

  it("has a KI tab whose choices are saved with the config", async () => {
    currentId = "7";
    aiStatus.mockResolvedValue([
      { topic_key: "support", label: "Support", queue_id: 1, queue_name: "Support", ai_mode: "off", ok: false, reason: "ai_mode_off" },
    ]);
    updateConfig.mockResolvedValue(saved);
    renderPage();
    fireEvent.click(await screen.findByRole("tab", { name: "KI" }));
    fireEvent.click(await screen.findByTestId("webchat-ai-topic-0-mode-first_line"));
    fireEvent.change(screen.getByTestId("webchat-bot-name"), { target: { value: " Frieda " } });
    fireEvent.change(screen.getByTestId("webchat-bot-tone"), { target: { value: "  " } });
    expect(await screen.findByTestId("webchat-ai-status-support-state")).toHaveTextContent("ausgeschaltet");
    expect(aiStatus).toHaveBeenCalledWith(7, expect.anything());

    fireEvent.click(screen.getByTestId("webchat-save"));
    await waitFor(() => expect(updateConfig).toHaveBeenCalledTimes(1));
    expect(updateConfig.mock.calls[0][1].values).toMatchObject({
      topics: [{ key: "support", ai_mode: "first_line" }],
      bot_name: "Frieda",
      bot_tone_prompt: null,
    });
  });

  it("jumps to the KI tab for a server error on the assistant name", async () => {
    currentId = "7";
    updateConfig.mockRejectedValueOnce(await apiError422({ bot_name: "Der Name ist ungültig." }));
    renderPage();
    fireEvent.click(await screen.findByRole("tab", { name: "Einbetten" }));
    await screen.findByTestId("webchat-tab-embed");
    fireEvent.click(screen.getByTestId("webchat-save"));
    expect(await screen.findByTestId("webchat-bot-name-error")).toHaveTextContent("Der Name ist ungültig.");
    expect(screen.getByTestId("webchat-tab-ai-error")).toBeInTheDocument();
  });

  it("embed tab: portal switch with its server error, website sign-in key and unknown customers", async () => {
    currentId = "7";
    updateConfig.mockRejectedValueOnce(
      await apiError422({ show_on_portal: "Es kann nur ein Chat im Kundenportal angezeigt werden." }),
    );
    createIdentitySecret.mockResolvedValue({ secret: "geheim-123" });
    renderPage();
    fireEvent.click(await screen.findByRole("tab", { name: "Einbetten" }));
    expect(await screen.findByText("Im Kundenportal anzeigen")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("webchat-show-on-portal"));
    fireEvent.click(screen.getByTestId("webchat-create-customers"));
    fireEvent.click(screen.getByTestId("webchat-save"));
    expect(await screen.findByTestId("webchat-show-on-portal-error")).toHaveTextContent(
      "Es kann nur ein Chat im Kundenportal angezeigt werden.",
    );
    expect(updateConfig.mock.calls[0][1].values).toMatchObject({ show_on_portal: true, identity_create_customers: true });

    expect(screen.getByText("Unbekannte Kunden anlegen")).toBeInTheDocument();
    fireEvent.click(screen.getByTestId("webchat-identity-create"));
    expect(await screen.findByTestId("webchat-identity-secret")).toHaveValue("geheim-123");
    // the section now knows a key is set, without a reload
    expect(screen.getByTestId("webchat-identity-regenerate")).toBeInTheDocument();
  });
});
