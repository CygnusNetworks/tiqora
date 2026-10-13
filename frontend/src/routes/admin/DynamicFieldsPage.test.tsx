import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { DynamicFieldsPage } from "./DynamicFieldsPage";

const list = vi.fn();
const create = vi.fn();
const pluginTypes = vi.fn();

vi.mock("@/lib/api", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    adminDynamicFields: {
      list: (...a: unknown[]) => list(...a),
      create: (...a: unknown[]) => create(...a),
      update: vi.fn(),
      deactivate: vi.fn(),
    },
  },
}));
vi.mock("@/lib/pluginPointsApi", () => ({
  pluginPointsApi: { dfPluginTypes: (...a: unknown[]) => pluginTypes(...a) },
}));

const CONTRACT_TYPE = {
  key: "Plugin_points_Contract",
  label: "Vertragsnummer (Test)",
  base_type: "Text",
  plugin_id: "org.example.points",
  config_schema: {
    type: "object",
    properties: { prefix: { type: "string", title: "Präfix" } },
  },
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <I18nextProvider i18n={i18n}>
        <DynamicFieldsPage />
      </I18nextProvider>
    </QueryClientProvider>,
  );
}

describe("DynamicFieldsPage plugin types", () => {
  beforeEach(async () => {
    for (const fn of [list, create, pluginTypes]) fn.mockReset();
    await i18n.changeLanguage("de");
    list.mockResolvedValue({ items: [], total: 0 });
    create.mockResolvedValue({});
  });

  it("creates a dynamic field with a plugin type", async () => {
    pluginTypes.mockResolvedValue([CONTRACT_TYPE]);
    renderPage();
    fireEvent.click(await screen.findByTestId("admin-new-button"));
    fireEvent.click(await screen.findByTestId("dynamic-field-plugin-type"));
    fireEvent.click(await screen.findByTestId("dynamic-field-plugin-type-menu-option-Plugin_points_Contract"));

    expect(screen.getByTestId("dynamic-field-type")).toBeDisabled();
    expect(screen.getByTestId("dynamic-field-type")).toHaveTextContent("Text");
    expect(screen.getByTestId("dynamic-field-plugin-type-hint")).toHaveTextContent(
      "Wird in Tiqora geprüft; in Znuny erscheint das Feld als „Text“.",
    );
    fireEvent.change(screen.getByLabelText("Präfix"), { target: { value: "V-" } });
    fireEvent.change(screen.getByTestId("dynamic-field-name"), { target: { value: "Vertrag" } });
    fireEvent.change(screen.getByTestId("dynamic-field-label"), { target: { value: "Vertragsnummer" } });
    fireEvent.change(screen.getByTestId("dynamic-field-order"), { target: { value: "10" } });
    fireEvent.click(screen.getByTestId("dynamic-field-form-submit"));

    await waitFor(() => expect(create).toHaveBeenCalledTimes(1));
    expect(create.mock.calls[0][0]).toMatchObject({
      name: "Vertrag",
      field_type: "Text",
      object_type: "Ticket",
      plugin_type: "Plugin_points_Contract",
      config: { TiqoraPluginConfig: { prefix: "V-" } },
    });
  });

  it("offers no plugin type select when no plugin brings one", async () => {
    pluginTypes.mockResolvedValue([]);
    renderPage();
    fireEvent.click(await screen.findByTestId("admin-new-button"));
    await screen.findByTestId("dynamic-field-type");
    expect(screen.queryByTestId("dynamic-field-plugin-type")).not.toBeInTheDocument();
    expect(screen.getByTestId("dynamic-field-type")).toBeEnabled();
  });
});
