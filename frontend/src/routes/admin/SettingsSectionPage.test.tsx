import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { SettingsSectionPage } from "./SettingsSectionPage";

let currentSection = "jobs";
vi.mock("@tanstack/react-router", () => ({
  useParams: () => ({ section: currentSection }),
}));

const get = vi.fn();
const put = vi.fn();
vi.mock("@/lib/settingsSectionsApi", async (orig) => ({
  ...(await orig<typeof import("@/lib/settingsSectionsApi")>()),
  settingsSectionsApi: {
    get: (...a: unknown[]) => get(...a),
    put: (...a: unknown[]) => put(...a),
    list: vi.fn(),
  },
}));

const jobsSection = {
  key: "jobs",
  title_key: "admin.settings.jobs.title",
  i18n_prefix: "admin.settings.jobs",
  json_schema: {
    properties: {
      max_concurrency: { default: 2, maximum: 8, minimum: 1, title: "Max Concurrency", type: "integer" },
      retention_days: { default: 14, maximum: 365, minimum: 1, title: "Retention Days", type: "integer" },
      secret_note: {
        anyOf: [{ type: "string", format: "password", writeOnly: true }, { type: "null" }],
        default: null,
        title: "Secret Note",
      },
    },
    title: "JobsSettings",
    type: "object",
  },
  values: { max_concurrency: 2, retention_days: 14, secret_note: "********" },
  secrets_set: ["secret_note"],
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <SettingsSectionPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("SettingsSectionPage", () => {
  beforeEach(() => {
    currentSection = "jobs";
    get.mockReset();
    put.mockReset();
    get.mockResolvedValue(jobsSection);
    put.mockResolvedValue(jobsSection);
  });

  it("renders the stored values and labels", async () => {
    renderPage();
    const input = await screen.findByTestId("schema-form-max_concurrency");
    expect(input).toHaveValue(2);
    expect(screen.getByTestId("schema-form-max_concurrency-label")).toHaveTextContent("Concurrent tasks");
    expect(screen.getByTestId("admin-settings-title")).toHaveTextContent("Background tasks");
  });

  it("sends only changed keys and never an untouched secret", async () => {
    renderPage();
    const input = await screen.findByTestId("schema-form-max_concurrency");
    fireEvent.change(input, { target: { value: "3" } });
    fireEvent.click(screen.getByTestId("admin-settings-save"));
    await waitFor(() => expect(put).toHaveBeenCalledWith("jobs", { max_concurrency: 3 }));
    expect(await screen.findByTestId("admin-settings-saved")).toBeInTheDocument();
  });

  it("sends a typed secret and an empty string for a cleared one", async () => {
    renderPage();
    const secret = await screen.findByTestId("schema-form-secret_note");
    fireEvent.change(secret, { target: { value: "neu" } });
    fireEvent.click(screen.getByTestId("admin-settings-save"));
    await waitFor(() => expect(put).toHaveBeenCalledWith("jobs", { secret_note: "neu" }));
  });

  it("sends an empty string when the stored secret is removed", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("schema-form-secret_note-clear"));
    fireEvent.click(screen.getByTestId("admin-settings-save"));
    await waitFor(() => expect(put).toHaveBeenCalledWith("jobs", { secret_note: "" }));
  });

  it("maps a 422 onto the field", async () => {
    put.mockRejectedValue(
      new ApiError(422, { detail: { message: "Bitte prüfen", errors: { max_concurrency: "zu groß" } } }, "/x"),
    );
    renderPage();
    fireEvent.change(await screen.findByTestId("schema-form-max_concurrency"), { target: { value: "9" } });
    fireEvent.click(screen.getByTestId("admin-settings-save"));
    expect(await screen.findByTestId("schema-form-max_concurrency-error")).toHaveTextContent("zu groß");
  });

  it("shows not-found for an unknown section and a load error otherwise", async () => {
    get.mockRejectedValue(new ApiError(404, { detail: "nope" }, "/x"));
    renderPage();
    expect(await screen.findByTestId("admin-settings-not-found")).toHaveTextContent(
      "These settings do not exist.",
    );
  });

  it("shows a load error", async () => {
    get.mockRejectedValue(new ApiError(500, "boom", "/x"));
    renderPage();
    expect(await screen.findByTestId("admin-settings-load-error")).toHaveTextContent(
      "The settings could not be loaded.",
    );
  });
});
