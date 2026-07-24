import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { TemplatesPage } from "./TemplatesPage";

const list = vi.fn();
const update = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    agentTemplates: {
      list: (...args: unknown[]) => list(...args),
      update: (...args: unknown[]) => update(...args),
    },
  },
}));

let canEditTemplates = true;
vi.mock("@/auth/AuthContext", () => ({
  useAuth: () => ({ user: { id: 1, login: "agent", can_edit_templates: canEditTemplates } }),
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <TemplatesPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("TemplatesPage (agent)", () => {
  beforeEach(() => {
    list.mockReset();
    update.mockReset();
    canEditTemplates = true;
    list.mockResolvedValue({
      items: [
        {
          id: 1,
          name: "Welcome",
          text: "hi there",
          content_type: null,
          template_type: "Answer",
          comments: "greeting",
          valid_id: 1,
          create_time: "2026-01-01T00:00:00Z",
          change_time: "2026-01-01T00:00:00Z",
        },
      ],
      total: 1,
      page: 1,
      page_size: 500,
    });
    update.mockResolvedValue({});
  });

  it("shows an access-denied message when the agent lacks edit rights", () => {
    canEditTemplates = false;
    renderPage();
    expect(screen.getByTestId("templates-access-denied")).toBeInTheDocument();
    expect(list).not.toHaveBeenCalled();
  });

  it("lists the agent's editable templates", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("template-row-1")).toBeInTheDocument();
    });
    expect(screen.getByTestId("template-row-1")).toHaveTextContent("Welcome");
  });

  it("opens the edit dialog and saves changes via the update API", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("template-edit-1")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("template-edit-1"));
    await waitFor(() => {
      expect(screen.getByTestId("template-edit-form")).toBeInTheDocument();
    });

    fireEvent.change(screen.getByTestId("template-form-name"), {
      target: { value: "Welcome Updated" },
    });
    fireEvent.click(screen.getByTestId("template-save"));

    await waitFor(() => {
      expect(update).toHaveBeenCalledWith(1, {
        name: "Welcome Updated",
        comments: "greeting",
        text: "hi there",
      });
    });
  });

  it("shows an empty state when there are no templates", async () => {
    list.mockResolvedValue({ items: [], total: 0, page: 1, page_size: 500 });
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("agent-templates-empty")).toBeInTheDocument();
    });
  });
});
