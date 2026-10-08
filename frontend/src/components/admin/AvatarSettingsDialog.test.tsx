import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { AvatarSettingsDialog } from "./AvatarSettingsDialog";

const get = vi.fn();
const put = vi.fn();
vi.mock("@/lib/settingsSectionsApi", async (orig) => ({
  ...(await orig<typeof import("@/lib/settingsSectionsApi")>()),
  settingsSectionsApi: { get: (...a: unknown[]) => get(...a), put: (...a: unknown[]) => put(...a), list: vi.fn() },
}));

const section = {
  key: "avatars",
  title_key: "admin.settings.avatars.title",
  i18n_prefix: "admin.settings.avatars",
  json_schema: {
    properties: {
      gravatar_enabled: { default: true, title: "Gravatar Enabled", type: "boolean" },
      favicon_enabled: { default: true, title: "Favicon Enabled", type: "boolean" },
    },
    title: "AvatarSettings",
    type: "object",
  },
  values: { gravatar_enabled: true, favicon_enabled: true },
  secrets_set: [],
};

function renderDialog(onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <AvatarSettingsDialog onClose={onClose} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return onClose;
}

describe("AvatarSettingsDialog", () => {
  beforeEach(() => {
    get.mockReset();
    put.mockReset();
    get.mockResolvedValue(section);
    put.mockResolvedValue(section);
  });

  it("toggles a source and saves only the change", async () => {
    const onClose = renderDialog();
    const sw = await screen.findByTestId("admin-avatars-gravatar_enabled");
    expect(screen.getByTestId("admin-avatars-gravatar_enabled-label")).toHaveTextContent("Gravatar");
    fireEvent.click(sw);
    fireEvent.click(screen.getByTestId("admin-avatars-save"));
    await waitFor(() => expect(put).toHaveBeenCalledWith("avatars", { gravatar_enabled: false }));
    await waitFor(() => expect(onClose).toHaveBeenCalled());
  });

  it("shows the load error", async () => {
    get.mockRejectedValue(new Error("x"));
    renderDialog();
    expect(await screen.findByTestId("admin-avatars-load-error")).toBeInTheDocument();
  });
});
