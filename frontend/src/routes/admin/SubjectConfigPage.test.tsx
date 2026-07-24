import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { SubjectConfigPage } from "./SubjectConfigPage";

const getSubjectConfig = vi.fn();
const putSubjectConfig = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getSubjectConfig: (...args: unknown[]) => getSubjectConfig(...args),
    putSubjectConfig: (...args: unknown[]) => putSubjectConfig(...args),
  },
}));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <SubjectConfigPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

const sampleConfig = {
  enabled: true,
  hook: "Ticket#",
  divider: "",
  subject_format: "Left",
  overrides: { enabled: null, hook: null, divider: null, subject_format: null },
  znuny: { hook: "Ticket#", divider: "", subject_format: "Left" },
};

describe("SubjectConfigPage", () => {
  beforeEach(() => {
    getSubjectConfig.mockReset();
    putSubjectConfig.mockReset();
    getSubjectConfig.mockResolvedValue(sampleConfig);
  });

  it("renders the current config and a live preview", async () => {
    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId("subject-config-hook")).toHaveValue("Ticket#");
    });
    expect(screen.getByTestId("subject-config-enabled")).toBeChecked();
    expect(screen.getByTestId("subject-config-preview")).toHaveTextContent(
      "[Ticket#2026070100000019] Re: Beispiel",
    );
  });

  it("updates the preview as the format changes to Right", async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("subject-config-hook")).toHaveValue("Ticket#");
    });

    fireEvent.click(screen.getByTestId("subject-config-format"));
    fireEvent.click(await screen.findByTestId("subject-config-format-menu-option-Right"));

    await waitFor(() => {
      expect(screen.getByTestId("subject-config-preview")).toHaveTextContent(
        "Re: Beispiel [Ticket#2026070100000019]",
      );
    });
  });

  it("saves edited hook/divider/format and sends the full payload", async () => {
    putSubjectConfig.mockResolvedValue({
      ...sampleConfig,
      hook: "Anfrage#",
      divider: ":",
      subject_format: "Right",
    });
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("subject-config-hook")).toHaveValue("Ticket#");
    });

    fireEvent.change(screen.getByTestId("subject-config-hook"), {
      target: { value: "Anfrage#" },
    });
    fireEvent.change(screen.getByTestId("subject-config-divider"), {
      target: { value: ":" },
    });
    fireEvent.click(screen.getByTestId("subject-config-format"));
    fireEvent.click(await screen.findByTestId("subject-config-format-menu-option-Right"));
    fireEvent.click(screen.getByTestId("subject-config-save"));

    await waitFor(() => {
      expect(putSubjectConfig).toHaveBeenCalledWith({
        enabled: true,
        hook: "Anfrage#",
        divider: ":",
        subject_format: "Right",
      });
    });
    await waitFor(() => {
      expect(screen.getByTestId("subject-config-status")).toBeInTheDocument();
    });
  });

  it("clears overrides via the reset-to-Znuny button", async () => {
    putSubjectConfig.mockResolvedValue(sampleConfig);
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("subject-config-reset")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("subject-config-reset"));

    await waitFor(() => {
      expect(putSubjectConfig).toHaveBeenCalledWith({
        enabled: null,
        hook: null,
        divider: null,
        subject_format: null,
      });
    });
  });

  it("shows a load error when the config fails to fetch", async () => {
    getSubjectConfig.mockRejectedValue(new Error("boom"));
    renderPage();

    await waitFor(() => {
      expect(screen.getByText("Failed to load settings.")).toBeInTheDocument();
    });
  });

  it("shows a save error when the mutation fails", async () => {
    putSubjectConfig.mockRejectedValue(new Error("nope"));
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId("subject-config-save")).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTestId("subject-config-save"));

    await waitFor(() => {
      expect(screen.getByTestId("subject-config-status")).toHaveTextContent("Failed to save settings.");
    });
  });
});
