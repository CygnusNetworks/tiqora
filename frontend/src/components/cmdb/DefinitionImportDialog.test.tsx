import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { I18nextProvider } from "react-i18next";
import i18n from "@/i18n";
import { ApiError } from "@/lib/api";
import { cmdbAdminApi } from "@/lib/cmdbAdminApi";
import { DefinitionImportDialog } from "./DefinitionImportDialog";

const ATTR = { key: "Name", name: "Name", type: "text", required: false, searchable: false, portal: false, count_min: 0, count_max: 1, count_default: 1, options: { multiline: false }, sub: [] };

function setup() {
  const onApply = vi.fn();
  const onClose = vi.fn();
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { mutations: { retry: false } } })}>
      <I18nextProvider i18n={i18n}>
        <DefinitionImportDialog open onClose={onClose} onApply={onApply} />
      </I18nextProvider>
    </QueryClientProvider>,
  );
  return { onApply, onClose };
}

beforeEach(async () => {
  await i18n.changeLanguage("de");
});
afterEach(() => vi.restoreAllMocks());

describe("DefinitionImportDialog", () => {
  it("cannot preview or apply an empty text", () => {
    setup();
    expect(screen.getByTestId("def-import-preview")).toBeDisabled();
    expect(screen.getByTestId("def-import-apply")).toBeDisabled();
  });

  it("previews with warnings and missing catalog classes, then hands the attributes over", async () => {
    const spy = vi.spyOn(cmdbAdminApi, "importDefinition").mockResolvedValue({
      attributes: [ATTR, { ...ATTR, key: "Type", name: "Typ", type: "catalog" }],
      warnings: ["Das Feld „Input“ wurde nicht erkannt."],
      missing_catalog_classes: ["ITSM::Demo::Type"],
    } as never);
    const { onApply } = setup();
    fireEvent.change(screen.getByTestId("def-import-text"), { target: { value: "[ { Key => 'Name' } ]" } });
    fireEvent.click(screen.getByTestId("def-import-preview"));
    expect(await screen.findByTestId("def-import-result")).toBeInTheDocument();
    expect(spy).toHaveBeenCalledWith({ format: "znuny_perl", text: "[ { Key => 'Name' } ]" });
    expect(screen.getByTestId("def-import-count")).toHaveTextContent("2 Attribut(e) erkannt.");
    expect(screen.getByTestId("def-import-warnings")).toHaveTextContent("nicht erkannt");
    expect(screen.getByTestId("def-import-missing")).toHaveTextContent("ITSM::Demo::Type");
    expect(onApply).not.toHaveBeenCalled();
    fireEvent.click(screen.getByTestId("def-import-apply"));
    expect(onApply).toHaveBeenCalledTimes(1);
    expect(onApply.mock.calls[0][0].map((a: { key: string }) => a.key)).toEqual(["Name", "Type"]);
  });

  it("offers the three formats and sends the chosen one", async () => {
    const spy = vi.spyOn(cmdbAdminApi, "importDefinition").mockResolvedValue({ attributes: [ATTR], warnings: [], missing_catalog_classes: [] } as never);
    setup();
    fireEvent.click(screen.getByTestId("def-import-format"));
    expect(screen.getByTestId("def-import-format-menu-option-znuny_perl")).toHaveTextContent("Znuny 6 (Perl)");
    expect(screen.getByTestId("def-import-format-menu-option-znuny_yaml")).toHaveTextContent("Znuny 7 (YAML)");
    fireEvent.click(screen.getByTestId("def-import-format-menu-option-tiqora_json"));
    fireEvent.change(screen.getByTestId("def-import-text"), { target: { value: "[]" } });
    fireEvent.click(screen.getByTestId("def-import-preview"));
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ format: "tiqora_json", text: "[]" }));
    expect(screen.queryByTestId("def-import-warnings")).not.toBeInTheDocument();
  });

  it("shows the server's message when the text cannot be parsed and keeps apply disabled", async () => {
    vi.spyOn(cmdbAdminApi, "importDefinition").mockRejectedValue(new ApiError(422, { detail: "Zeile 3: unerwartetes Zeichen." }, "/api/v1/admin/cmdb/definition-import"));
    setup();
    fireEvent.change(screen.getByTestId("def-import-text"), { target: { value: "garbage" } });
    fireEvent.click(screen.getByTestId("def-import-preview"));
    expect(await screen.findByTestId("def-import-error")).toHaveTextContent("Zeile 3");
    expect(screen.getByTestId("def-import-apply")).toBeDisabled();
  });

  it("reads a chosen file into the text field and drops a stale preview when the text changes", async () => {
    vi.spyOn(cmdbAdminApi, "importDefinition").mockResolvedValue({ attributes: [ATTR], warnings: [], missing_catalog_classes: [] } as never);
    setup();
    const file = new File(["- Key: Name"], "def.yml", { type: "text/yaml" });
    fireEvent.change(screen.getByTestId("def-import-file"), { target: { files: [file] } });
    await waitFor(() => expect(screen.getByTestId("def-import-text")).toHaveValue("- Key: Name"));
    fireEvent.click(screen.getByTestId("def-import-preview"));
    await screen.findByTestId("def-import-result");
    fireEvent.change(screen.getByTestId("def-import-text"), { target: { value: "- Key: Other" } });
    expect(screen.queryByTestId("def-import-result")).not.toBeInTheDocument();
    expect(screen.getByTestId("def-import-apply")).toBeDisabled();
  });
});
